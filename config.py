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
        # Asegurar ruta absoluta relativa al archivo config.py
        base_path = os.path.dirname(os.path.abspath(__file__))
        vault_path = os.path.join(base_path, 'companies.json')
        
        if not os.path.exists(vault_path):
            raise FileNotFoundError(f"No se encontró el banco de credenciales en {vault_path}.")

        try:
            with open(vault_path, 'r', encoding='utf-8') as f:
                companies = json.load(f)
        except Exception as e:
            raise Exception(f"Error al leer el banco de credenciales JSON: {e}")

        # Normalizar URL para comparación (quitar "/" al final)
        search_url = url.rstrip('/')
        
        for company in companies:
            config_url = company.get('url', '').rstrip('/')
            if (config_url == search_url and 
                company.get('db') == db and 
                company.get('user') == user):
                return company.get('password')

        raise ValueError(f"No se encontró contraseña para {user} en {db} ({url}) en el banco local de la VPS.")

