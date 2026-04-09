import sys
import os

# Añadir el raíz de la API al path
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)

from database.odoo_client import x_OdooClient
from repositories.payroll_repo import x_PayrollRepository
from config import Config

def test_config():
    url = "https://codyd-pruebas8426.odoo.com/"
    db = "codyd-pruebas8426"
    user = "odoo@codyd.com.co"
    password = Config.x_resolve_password(url, db, user)
    
    print(f"Conectando a {url}...")
    client = x_OdooClient(url, db, user, password)
    repo = x_PayrollRepository(client)
    
    # Supongamos Company ID 1
    print("Obteniendo configuración DIAN...")
    config = repo.x_get_dian_configuration(1)
    print("\nRESULTADO:")
    print(f"Software ID: {config['dian'].get('software_id')}")
    print(f"Software PIN: {config['dian'].get('software_pin')}")
    print(f"Testing ID: {config['dian'].get('testing_id')}")
    print(f"Operation Mode: {config['dian'].get('operation_mode')}")

if __name__ == "__main__":
    test_config()
