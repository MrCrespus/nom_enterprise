import sys
import argparse
from database.odoo_client import x_OdooClient
from repositories.payroll_repo import x_PayrollRepository
from services.dian_client import x_DianClient
from logger_config import x_setup_logging
import xml.etree.ElementTree as ET

def parse_args():
    parser = argparse.ArgumentParser(description='Odoo DIAN Status Checker')
    parser.add_argument('track_id', type=str, help='El ZipKey o TrackId a consultar en la DIAN')
    parser.add_argument('--credentials', type=str, help='Odoo credentials (URL|||DB|||User|||Pass)')
    parser.add_argument('--company', type=int, default=1, help='ID de la compañía (por defecto 1)')
    return parser.parse_args()

def main():
    logger = x_setup_logging('check_status')
    try:
        args = parse_args()
        track_id = args.track_id
        
        odoo_url, odoo_db, odoo_username, odoo_password = None, None, None, None
        if args.credentials:
            parts = args.credentials.strip("'\"").split('|||')
            if len(parts) == 4:
                odoo_url, odoo_db, odoo_username, odoo_password = parts

        logger.info("Conectando a Odoo para recuperar certificado...")
        client = x_OdooClient(url=odoo_url, db=odoo_db, username=odoo_username, password=odoo_password)
        repo = x_PayrollRepository(client)
        
        dian_settings = repo.x_get_dian_configuration(args.company)
        cert_data = dian_settings.get('certificate', {})
        
        if not cert_data:
            logger.error("No se pudo obtener el certificado de Odoo. No se puede firmar la consulta.")
            sys.exit(1)

        dian_client = x_DianClient()
        logger.info(f"Enviando consulta GetStatusZip para TrackId: {track_id}")
        
        response = dian_client.x_get_status_zip(track_id, dian_settings.get('dian', {}), cert_data)
        
        logger.info("=" * 60)
        logger.info(f"RESULTADO: {response['message']}")
        logger.info("=" * 60)
        
        company_info = client.x_execute('res.company', 'read', [args.company], fields=['name'])
        company_name = company_info[0]['name'] if company_info else f"Company_{args.company}"
        
        # Cambiamos al logger de la empresa
        logger = x_setup_logging('check_status', company_name=company_name)

        import re, os
        safe_company = re.sub(r'[^\w\s-]', '', company_name).strip().replace(' ', '_')
        target_dir = os.path.join('output_xmls', safe_company)
        
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)
        
        output_path = os.path.join(target_dir, f"STATUS_{track_id}.xml")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(response['dian_response_xml'])
        logger.info(f"Respuesta XML guardada en {output_path}")

    except Exception as e:
        logger.error(f"Error crítico: {e}")

if __name__ == "__main__":
    main()
