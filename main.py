import sys
import os
import argparse
import traceback
from database.odoo_client import x_OdooClient
from repositories.payroll_repo import x_PayrollRepository
from services.dian_mapper import x_DianMapper
from services.xml_generator import x_XMLGenerator
from services.cune_calculator import x_CuneCalculator
from logger_config import x_setup_logging
from config import Config

def parse_args():
    parser = argparse.ArgumentParser(description='Odoo Payroll DIAN Bridge')
    parser.add_argument('--credentials', type=str, help='Odoo credentials in format URL|||DB|||User|||Pass')
    return parser.parse_args()

def action_generate_xml(repo, xml_gen, logger, date_start=None, date_end=None):
    logger.info(f"Iniciando generación de XMLs desde Odoo para {date_start or 'todos'}...")
    slips_to_process = repo.x_get_slips_for_dian(date_start, date_end)
    
    if not slips_to_process:
        logger.info("No se encontraron nóminas pendientes de envío (estado 'validated'/'paid' sin DIAN status 'sent').")
        return

    for slip_id in slips_to_process:
        try:
            full_slip_data = repo.x_get_full_data_for_xml(slip_id)
            company_name = full_slip_data['company']['name']
            
            # Cambiamos a un logger específico por empresa
            logger = x_setup_logging('main', company_name=company_name)
            repo.logger = x_setup_logging('payroll_repo', company_name=company_name)
            
            company_id = full_slip_data['slip']['company_id'][0]
            dian_settings = repo.x_get_dian_configuration(company_id)

            logger.info(f"Generando XML para Payslip ID: {slip_id}")
            logger.info(f"Mapeando datos a estructura DIAN para Payslip ID: {slip_id}")
            # Nota: Usamos calculations=None para que tome lo que hay en las líneas reales de Odoo
            dian_json = x_DianMapper.x_to_dian_structure(
                full_slip_data, dian_settings,
                overtime_hours=full_slip_data.get('manual_inputs', {}))

            pin = dian_settings.get('dian', {}).get('software_pin', '75315')
            logger.info(f"Calculando CUNE para Payslip ID: {slip_id}")
            dian_json['CUNE'] = x_CuneCalculator.x_calculate(dian_json, pin_software=pin)

            logger.info(f"Generando XML para Payslip ID: {slip_id}")
            xml_str = xml_gen.x_render(dian_json)
            cert_data = dian_settings.get('certificate', {})

            try:
                from services.signature_service import x_SignatureService
                signer = x_SignatureService()
                signed = False

                if cert_data.get('private_key_pem'):
                    try:
                        logger.info(f"Firmando con PEM para Payslip ID: {slip_id}")
                        xml_str = signer.x_sign_with_pem(
                            xml_str,
                            cert_data['private_key_pem'],
                            cert_data.get('public_key_pem'),
                            cert_data.get('password')
                        )
                        signed = True
                    except Exception:
                        logger.warning(f"Error al firmar con PEM para Payslip ID: {slip_id}")

                if not signed and cert_data.get('content') and cert_data.get('password'):
                    logger.info(f"Firmando con PKCS12 para Payslip ID: {slip_id}")
                    xml_str = signer.x_sign(
                        xml_str, cert_data['content'], cert_data['password'])
                    signed = True

                if signed:
                    logger.info(f"XML firmado exitosamente para Payslip ID: {slip_id}")
                else:
                    logger.error(f"No se pudo firmar el XML para Payslip ID: {slip_id}")

            except Exception:
                logger.error(f"Error crítico al intentar firmar el XML para Payslip ID: {slip_id}")

            numero_nomina = dian_json['NumeroSecuenciaXML']['Numero']
            odoo_filename = f"{numero_nomina}.xml"
            safe_numero = str(numero_nomina).replace('/', '_').replace('\\', '_')
            local_filename = f"{safe_numero}.xml"

            import re
            company_name = full_slip_data['company']['name']
            safe_company = re.sub(r'[^\w\s-]', '', company_name).strip().replace(' ', '_')
            
            # Definir directorio de salida (Ruta absoluta basada en este archivo)
            base_path = os.path.dirname(os.path.abspath(__file__))
            target_dir = os.path.join(base_path, 'output_xmls', safe_company)

            xml_gen.x_save_to_file(xml_str, local_filename, output_dir=target_dir)
            logger.info(f"XML guardado localmente en {target_dir} como {local_filename}")

            try:
                from services.dian_client import x_DianClient
                dian_client = x_DianClient()
                logger.info(f"Iniciando envío a la DIAN para Payslip ID: {slip_id}")
                dian_response = dian_client.x_send_to_dian(xml_str, odoo_filename, dian_settings.get('dian', {}), cert_data, output_dir=target_dir)
                
                logger.info(f"Resultado de envío DIAN: {dian_response['message']}")
                
                # Save DIAN response locally
                resp_local_filename = f"RESP_{safe_numero}.xml" 
                xml_gen.x_save_to_file(dian_response['dian_response_xml'], resp_local_filename, output_dir=target_dir)
                logger.info(f"Respuesta DIAN guardada localmente en {target_dir} como {resp_local_filename}")
                
                # Automatically check status if zip_key is retrieved (with retries if pending)
                status_response = None
                status_local_filename = None
                if dian_response.get('zip_key'):
                    zip_key = dian_response['zip_key']
                    logger.info(f"Consultando estado automáticamente para TrackId/ZipKey: {zip_key}")
                    
                    import time
                    max_retries = 3
                    for i in range(max_retries):
                        status_response = dian_client.x_get_status_zip(zip_key, dian_settings.get('dian', {}), cert_data)
                        logger.info(f"Resultado estado DIAN (Intento {i+1}): {status_response['message']}")
                        
                        if "Batch en proceso de validación" in status_response['message']:
                            if i < max_retries - 1:
                                logger.info("El documento sigue en proceso. Reintentando en 10 segundos...")
                                time.sleep(10)
                                continue
                        break # Exit loop if not pending or max retries reached
                    
                    status_local_filename = f"STATUS_{safe_numero}.xml"
                    xml_gen.x_save_to_file(status_response['dian_response_xml'], status_local_filename, output_dir=target_dir)
                    logger.info(f"Status guardado localmente en {target_dir} como {status_local_filename}")
                
            except Exception as e:
                logger.error(f"Error enviando XML a la DIAN para Payslip ID {slip_id}: {e}")
                dian_response = {'dian_response_xml': f'<Error>{str(e)}</Error>'}
                resp_local_filename = None
                status_response = None

            try:
                logger.info(f"Subiendo XMLs a Odoo para Payslip ID: {slip_id}")
                attachment_ids = []
                
                # 1. XML de Nómina (Formato solicitado: SLIP_XXX.xml)
                att_id = repo.x_upload_xml_to_odoo(slip_id, local_filename, xml_str)
                if att_id: attachment_ids.append(att_id)
                
                # 2. XML de Respuesta Técnica DIAN (RESP_XXX.xml)
                if resp_local_filename:
                    repo.x_upload_xml_to_odoo(slip_id, resp_local_filename, dian_response['dian_response_xml'])
                
                # 3. XML de Estado Final DIAN (STATUS_{numero}.xml)
                if status_response and status_local_filename:
                    repo.x_upload_xml_to_odoo(slip_id, status_local_filename, status_response['dian_response_xml'])

                # Publicar Nota en el Chatter con los 3 archivos
                if attachment_ids:
                    # status_response puede ser None si la DIAN falló o no retornó estado
                    if status_response:
                        status_text = status_response.get('message', 'Sin respuesta de la DIAN')
                        clean_status = status_text.split(' (Code:')[0] if ' (Code:' in status_text else status_text
                    else:
                        clean_status = 'Error al enviar a la DIAN — XML adjunto para revisión manual'

                    body = f"Respuesta DIAN: {clean_status}"

                    repo.x_post_message('hr.payslip', slip_id, body, attachment_ids=attachment_ids)

                    # Actualizar campos de estado en Odoo (Studio)
                    is_ok = status_response.get('is_success', False) if status_response else False
                    dian_status_key = 'sent' if is_ok else 'rejected'
                    repo.x_update_dian_fields(slip_id, dian_status_key, zip_key=dian_response.get('zip_key') if dian_response else None)

            except Exception as e:
                logger.error(f"Error al subir XMLs o publicar nota en Odoo para Payslip ID: {slip_id}: {e}")

        except Exception:
            logger.error(f"Error procesando XML para Payslip ID {slip_id}: {traceback.format_exc()}")

def main():
    logger = x_setup_logging('main')
    logger.info("Iniciando proceso de nómina modular...")
    try:
        args = parse_args()
        odoo_url, odoo_db, odoo_username, odoo_password = None, None, None, None

        if args.credentials:
            try:
                clean_creds = args.credentials.strip("'\"")
                parts = clean_creds.split('|||')
                if len(parts) == 4:
                    odoo_url, odoo_db, odoo_username, odoo_password = parts
                elif len(parts) == 3:
                    odoo_url, odoo_db, odoo_username = parts
                    logger.info(f"Resolviendo contraseña local para {odoo_username} en {odoo_db}...")
                    odoo_password = Config.x_resolve_password(odoo_url, odoo_db, odoo_username)
                else:
                    logger.error("Formato de credenciales inválido. Debe ser URL|||DB|||User|||Pass o URL|||DB|||User (usando bóveda).")
                    sys.exit(1)
            except Exception as e:
                logger.error(f"Error al procesar credenciales: {e}")
                sys.exit(1)

        client = x_OdooClient(url=odoo_url, db=odoo_db,
                            username=odoo_username, password=odoo_password)
        repo = x_PayrollRepository(client)
        xml_gen = x_XMLGenerator()
 
        # Obtener período dinámico desde Odoo
        date_start, date_end = repo.x_get_current_reporting_period()
 
        # Ejecutar generación y comunicación DIAN (Solo Lectura)
        action_generate_xml(repo, xml_gen, logger, date_start, date_end)

        logger.info("Proceso finalizado exitosamente.")

    except Exception:
        logger.error(f"Error crítico en ejecución principal: {traceback.format_exc()}")

if __name__ == "__main__":
    main()
