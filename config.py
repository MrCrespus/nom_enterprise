import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class Config:
    ODOO_URL = os.getenv('ODOO_URL')
    ODOO_DB = os.getenv('ODOO_DB')
    ODOO_USERNAME = os.getenv('ODOO_USERNAME')
    ODOO_PASSWORD = os.getenv('ODOO_PASSWORD')

    CONCEPTO_MAP = {
        'BASIC': 'Basico',
        'AUX_TRANS': 'Transporte',
        'HED': 'HED',
        'HEN': 'HEN',
        'HEDD': 'HEDDF',
        'HEND': 'HENDF',
        'RN': 'HRN',
        'RDF': 'HRDDF',
        'RNDF': 'HRNDF',
        'SALUD': 'Salud',
        'EMP_PENSION': 'Pension',
        'EMB_ALI': 'OtrasDeducciones',
        'EMB_GEN': 'OtrasDeducciones',
        'COMIS': 'Comisiones',
        'LEAVE110': 'Incapacidades',
        'LEAVE120': 'Vacaciones',
        'ATTACH_SALARY': 'OtrasDeducciones',
        'ASSIG_SALARY': 'OtrasDeducciones',
        'DEDUCTION': 'OtrasDeducciones',
        'REIMBURSEMENT': 'OtrosConceptos'
    }

    PORCENTAJES_EXTRA = {
        'HED': 25.00,
        'HEN': 75.00,
        'HEDD': 100.00,
        'HEND': 150.00,
        'RN': 35.00,
        'RDF': 75.00,
        'RNDF': 110.00,
    }

    @staticmethod
    def x_resolve_password(url, db, user):
        """Busca la contraseña en el archivo local companies.json"""
        import json
        vault_path = 'companies.json'
        
        if not os.path.exists(vault_path):
            raise FileNotFoundError(f"No se encontró el banco de credenciales en {vault_path}. Por favor créalo basándote en la plantilla.")

        try:
            with open(vault_path, 'r', encoding='utf-8') as f:
                companies = json.load(f)
        except Exception as e:
            raise Exception(f"Error al leer el banco de credenciales JSON: {e}")

        # Buscamos la coincidencia exacta
        for company in companies:
            if (company.get('url') == url and 
                company.get('db') == db and 
                company.get('user') == user):
                return company.get('password')

        raise ValueError(f"No se encontró ninguna contraseña para el usuario {user} en la base {db} ({url}) en el banco de credenciales local.")

