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

