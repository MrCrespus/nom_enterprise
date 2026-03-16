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
        'SUELDO': 'Basico',
        'EXT_BASICO': 'Basico',

        'AUX_TRANS': 'Transporte',
        'EXT_TRANS': 'Transporte',

        'HED': 'HED',
        'HEN': 'HEN',
        'RNOC': 'HRN',
        'HEFD': 'HEDDF',
        'HEFN': 'HENDF',
        'REC_NOC': 'HRN',
        'EXT_RNOC': 'HRN',

        'SALUD': 'Salud',
        'SS_SALUD': 'Salud',
        'PENSION': 'Pension',
        'SS_PENSION': 'Pension',
        'FSP': 'FondoSolidaridad',
        'EXT_FSP': 'FondoSolidaridad'
    }

    PORCENTAJES_EXTRA = {
        'HED': 25.00,
        'HEN': 75.00,
        'HRN': 35.00,
        'HEDDF': 100.00,
        'HENDF': 150.00
    }

    PROVEEDOR_RAZON_SOCIAL = "CODYD SAS"
    PROVEEDOR_NIT = "900000000"
    PROVEEDOR_DV = "1"
    PROVEEDOR_SOFTWARE_ID = "00000000-0000-0000-0000-000000000000"
    PROVEEDOR_SOFTWARE_PIN = "75315"
