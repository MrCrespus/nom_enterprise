import sys
import argparse
import traceback
from database.odoo_client import OdooClient
from repositories.payroll_repo import PayrollRepository
from services.colombia_payroll_engine import ColombiaPayrollEngine
from services.dian_mapper import DianMapper
from services.xml_generator import XMLGenerator
from services.cune_calculator import CuneCalculator

FECHA_INICIO = '2026-01-01'
FECHA_FIN = '2026-12-31'


def parse_args():
    parser = argparse.ArgumentParser(description='Odoo Payroll API')
    parser.add_argument('--credentials', type=str,
                        help='Odoo credentials in format: url|||db|||username|||password')
    return parser.parse_args()


def main():
    try:
        args = parse_args()
        odoo_url = None
        odoo_db = None
        odoo_username = None
        odoo_password = None

        if args.credentials:
            try:
                # Remove quotes if they were included in the string by mistake
                clean_creds = args.credentials.strip("'\"")
                parts = clean_creds.split('|||')
                if len(parts) == 4:
                    odoo_url, odoo_db, odoo_username, odoo_password = parts
                    print(
                        f"✅ Usando credenciales dinámicas de webhook para: {odoo_url} / {odoo_db}")
                else:
                    print(
                        "❌ Error: El formato de credenciales debe ser url|||db|||username|||password")
                    sys.exit(1)
            except Exception as e:
                print(f"❌ Error parseando credenciales: {e}")
                sys.exit(1)

        # If strict mode is expected, we could force exit if no creds are passed.
        # But for now, we pass what we have (None or values) to OdooClient.
        # The OdooClient will fallback to Config if they are None, preserving local execution capability.

        client = OdooClient(url=odoo_url, db=odoo_db,
                            username=odoo_username, password=odoo_password)
        repo = PayrollRepository(client)
        engine = ColombiaPayrollEngine()
        xml_gen = XMLGenerator()

        print(
            f"--- GESTOR DE NÓMINA + DIAN ({FECHA_INICIO} al {FECHA_FIN}) ---")

        print("\n>>> FASE 1: Buscando contratos activos...")
        active_contracts = repo.get_active_contracts(FECHA_INICIO, FECHA_FIN)

        print(f"    Se encontraron {len(active_contracts)} contratos activos.")

        created_count = 0
        for contract in active_contracts:
            emp_id = contract['employee_id'][0]
            emp_name = contract['employee_id'][1]
            contract_id = contract['id']

            print(f"    + Creando nómina para: {emp_name}...")
            repo.create_payslip(contract_id, emp_id,
                                FECHA_INICIO, FECHA_FIN)
            created_count += 1

        print(
            f"    Generación finalizada. Se crearon {created_count} recibos nuevos.")

        print("\n>>> FASE 2: Calculando nóminas y Generando XML...")

        draft_ids = repo.get_draft_payslips()

        if not draft_ids:
            print("    No hay nóminas pendientes.")
            return

        for slip_id in draft_ids:
            try:
                print(f"\n    > Procesando ID {slip_id}...")

                # Recompute to trigger standard name/number generation if needed
                repo.client.execute('hr.payslip', 'compute_sheet', [slip_id])

                raw_data = repo.get_payslip_raw_data(slip_id)

                calculations = engine.calculate_payroll(
                    contract=raw_data['contract'],
                    worked_days_data=raw_data['worked_days'],
                    overtime_hours=raw_data['manual_inputs']
                )

                repo.write_calculations(slip_id, calculations)
                print(f"      ✅ Cálculos inyectados en Odoo.")

                print(f"      ⚙️ Generando XML Electrónico...")

                full_slip_data = repo.get_full_data_for_xml(slip_id)

                full_slip_data = repo.get_full_data_for_xml(slip_id)

                # Fetch DIAN Config for the company of this payslip
                company_id = full_slip_data['slip']['company_id'][0]
                dian_settings = repo.get_dian_configuration(company_id)

                dian_json = DianMapper.to_dian_structure(
                    full_slip_data, dian_settings)

                pin = dian_settings.get('dian', {}).get(
                    'software_pin', '75315')
                dian_json['CUNE'] = CuneCalculator.calculate(
                    dian_json, pin_software=pin)

                xml_str = xml_gen.render(dian_json)

                # Digital Signature
                print(f"      [Main] Firmando XML para nómina {slip_id}...")
                cert_data = dian_settings.get('certificate', {})
                try:
                    from services.signature_service import SignatureService
                    signer = SignatureService()

                    signed = False

                    # Try PEM first (if available)
                    if cert_data.get('private_key_pem'):
                        print(
                            f"      [Main] Intentando usar llave privada PEM...")
                        try:
                            # If public_key_pem is missing, pass None, and service will try to find certs in private_key_pem
                            xml_str = signer.sign_with_pem(
                                xml_str,
                                cert_data['private_key_pem'],
                                cert_data.get('public_key_pem'),
                                cert_data.get('password')
                            )
                            signed = True
                            print(
                                f"      [Main] XML firmado exitosamente con llaves PEM.")
                        except Exception as e:
                            print(
                                f"      ⚠️ Falló firma con PEM (Posiblemente formato incorrecto/imagen): {e}")
                            print(
                                f"      [Main] Intentando fallback a archivo PKCS12...")

                    # Fallback to PKCS12 if not signed yet
                    if not signed and cert_data.get('content') and cert_data.get('password'):
                        print(f"      [Main] Usando archivo PKCS12...")
                        xml_str = signer.sign(
                            xml_str, cert_data['content'], cert_data['password'])
                        signed = True
                        print(
                            f"      [Main] XML firmado exitosamente con PKCS12.")

                    if not signed:
                        print(
                            f"      ❌ ERROR: No se pudo firmar el XML (Fallaron PEM y PKCS12 o no hay datos).")

                except Exception as e:
                    print(f"      ❌ ERROR CRÍTICO al firmar XML: {e}")

                numero_nomina = dian_json['NumeroSecuenciaXML']['Numero']
                odoo_filename = f"{numero_nomina}.xml"
                safe_numero = str(numero_nomina).replace(
                    '/', '_').replace('\\', '_')
                local_filename = f"{safe_numero}.xml"

                file_path = xml_gen.save_to_file(
                    xml_str, local_filename)

                print(f"      📄 XML Creado localmente: {file_path}")

                try:
                    repo.x_upload_xml_to_odoo(slip_id, odoo_filename, xml_str)
                except Exception as upload_err:
                    print(
                        f"      ❌ ERROR al retornar XML a Odoo: {upload_err}")

            except Exception as e:
                print(f"      ❌ Error en ID {slip_id}: {e}")
                traceback.print_exc()

        print("\n--- PROCESO COMPLETADO ---")
        print("Revisa la carpeta 'output_xmls' para ver los archivos generados.")

    except Exception as e:
        print(f"Error crítico en Main: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
