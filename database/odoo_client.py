import xmlrpc.client
import ssl
from config import Config

class x_OdooClient:
    def __init__(self, url=None, db=None, username=None, password=None):
        self.url = url or Config.ODOO_URL
        self.db = db or Config.ODOO_DB
        self.username = username or Config.ODOO_USERNAME
        self.password = password or Config.ODOO_PASSWORD
        self.uid = None
        self.models = None
        self.x_connect()

    def x_connect(self):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        common = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/common', context=context)
        self.uid = common.authenticate(self.db, self.username, self.password, {})
        self.models = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object', context=context)

        if not self.uid:
            raise Exception("Error de autenticación: Verifica tus credenciales.")

    def x_execute(self, model, method, *args, **kwargs):
        return self.models.execute_kw(
            self.db, self.uid, self.password,
            model, method, list(args), kwargs
        )
