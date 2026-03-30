import sys
import argparse
import traceback
from database.odoo_client import x_OdooClient
from repositories.payroll_repo import x_PayrollRepository
from services.colombia_payroll_engine import x_ColombiaPayrollEngine
from services.dian_mapper import x_DianMapper
from services.xml_generator import x_XMLGenerator
from services.cune_calculator import x_CuneCalculator
from logger_config import x_setup_logging

FECHA_INICIO = '2026-03-01'
FECHA_FIN = '2026-03-31'

def parse_args():
    parser = argparse.ArgumentParser(description='Odoo Payroll API')
    parser.add_argument('--credentials', type=str)
    return parser.parse_args()

def main():
    logger = x_setup_logging('main')
    logger.info("Iniciando proceso de nómina...")
    try:
        args = parse_args()
        odoo_url = None
        odoo_db = None
        odoo_username = None
        odoo_password = None

        if args.credentials:
            try:
                clean_creds = args.credentials.strip("'\"")
                parts = clean_creds.split('|||')
                if len(parts) == 4:
                    odoo_url, odoo_db, odoo_username, odoo_password = parts
                else:
                    logger.error("Formato de credenciales inválido.")
                    sys.exit(1)
            except Exception:
                logger.error("Error al procesar credenciales.")
                sys.exit(1)

        client = x_OdooClient(url=odoo_url, db=odoo_db,
                            username=odoo_username, password=odoo_password)
        repo = x_PayrollRepository(client)
        engine = x_ColombiaPayrollEngine()
        xml_gen = x_XMLGenerator()

        logger.info(f"Buscando contratos activos entre {FECHA_INICIO} y {FECHA_FIN}...")
        active_contracts = repo.x_get_active_contracts(FECHA_INICIO, FECHA_FIN)
        logger.info(f"Se encontraron {len(active_contracts)} contratos activos.")

        slips_to_process = []
        for contract in active_contracts:
            emp_id = contract['employee_id'][0]
            contract_id = contract['id']
            logger.info(f"Creando/Verificando nómina para empleado ID: {emp_id}")
            slip_id = repo.x_create_payslip(contract_id, emp_id, FECHA_INICIO, FECHA_FIN)
            if slip_id:
                slips_to_process.append(slip_id)

        if not slips_to_process:
            logger.info("No hay nóminas para procesar.")
            return

        logger.info(f"Procesando {len(slips_to_process)} nóminas.")

        for slip_id in slips_to_process:
            try:
                logger.info(f"Procesando Payslip ID: {slip_id}")
                repo.client.x_execute('hr.payslip', 'compute_sheet', [slip_id])
                raw_data = repo.x_get_payslip_raw_data(slip_id)

                logger.info(f"Calculando conceptos para el Payslip ID: {slip_id}")
                calculations = engine.x_calculate_payroll(
                    contract=raw_data['contract'],
                    worked_days_data=raw_data['worked_days'],
                    overtime_hours=raw_data['manual_inputs']
                )

                logger.info(f"Inyectando {len(calculations)} cálculos en Odoo para Payslip ID: {slip_id}")
                repo.x_write_calculations(slip_id, calculations)
                
                full_slip_data = repo.x_get_full_data_for_xml(slip_id)
                company_id = full_slip_data['slip']['company_id'][0]
                dian_settings = repo.x_get_dian_configuration(company_id)

                # Obtener de nuevo los adjuntos ya que x_write_calculations pudo haber creado nuevos
                updated_raw_data = repo.x_get_payslip_raw_data(slip_id)
                
                logger.info(f"Mapeando datos a estructura DIAN para Payslip ID: {slip_id}")
                dian_json = x_DianMapper.x_to_dian_structure(
                    full_slip_data, dian_settings,
                    calculations=calculations,
                    overtime_hours=updated_raw_data['manual_inputs'],
                    attachments=updated_raw_data.get('attachments', []))

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
                local_filename = f"{safe_numero}_{slip_id}.xml"

                xml_gen.x_save_to_file(xml_str, local_filename)
                logger.info(f"XML guardado localmente como {local_filename}")

                try:
                    logger.info(f"Subiendo XML a Odoo para Payslip ID: {slip_id}")
                    repo.x_upload_xml_to_odoo(slip_id, odoo_filename, xml_str)
                except Exception:
                    logger.error(f"Error al subir XML a Odoo para Payslip ID: {slip_id}")

            except Exception:
                logger.error(f"Error procesando Payslip ID {slip_id}: {traceback.format_exc()}")

    except Exception:
        logger.error(f"Error crítico en ejecución principal: {traceback.format_exc()}")

if __name__ == "__main__":
    main()
