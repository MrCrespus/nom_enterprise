from database.odoo_client import OdooClient
import datetime


class PayrollRepository:
    def __init__(self, client: OdooClient):
        self.client = client

    def get_payslip_raw_data(self, payslip_id):
        slip = self.client.execute(
            'hr.payslip', 'read', [payslip_id],
            fields=['contract_id', 'worked_days_line_ids',
                    'input_line_ids', 'number', 'employee_id']
        )[0]

        contract = self.client.execute(
            'hr.contract', 'read', [slip['contract_id'][0]],
            fields=['wage', 'display_name']
        )[0]

        wd_lines = []
        if slip['worked_days_line_ids']:
            wd_lines = self.client.execute(
                'hr.payslip.worked_days', 'read', slip['worked_days_line_ids'],
                fields=['code', 'number_of_days']
            )

        worked_days_map = {item['code']: item['number_of_days']
                           for item in wd_lines}

        input_lines = []
        if slip['input_line_ids']:
            input_lines = self.client.execute(
                'hr.payslip.input', 'read', slip['input_line_ids'],
                fields=['code', 'amount']
            )

        inputs_map = {}
        for inp in input_lines:
            clean_code = inp['code'].replace('_QTY', '')
            inputs_map[clean_code] = inp['amount']

        return {
            'payslip_number': slip.get('number'),
            'contract': contract,
            'worked_days': worked_days_map,
            'manual_inputs': inputs_map
        }

    def write_calculations(self, payslip_id, calculated_values):
        print(f"      [Repo] Actualizando Odoo Nómina ID {payslip_id}...")

        existing_inputs = self.client.execute(
            'hr.payslip.input', 'search_read',
            [['payslip_id', '=', payslip_id]],
            fields=['code', 'id']
        )

        input_code_to_id = {x['code']: x['id'] for x in existing_inputs}

        for code, amount in calculated_values.items():
            if code in input_code_to_id:
                line_id = input_code_to_id[code]
                self.client.execute(
                    'hr.payslip.input', 'write',
                    [line_id],
                    {'amount': amount}
                )
            else:
                input_type_ids = self.client.execute(
                    'hr.payslip.input.type', 'search',
                    [['code', '=', code]],
                    limit=1
                )
                if input_type_ids:
                    new_vals = {
                        'payslip_id': payslip_id,
                        'input_type_id': input_type_ids[0],
                        'amount': amount,
                    }
                    self.client.execute(
                        'hr.payslip.input', 'create', [new_vals]
                    )
                    print(
                        f"      [Repo] Input '{code}' creado en nómina {payslip_id}.")
                else:
                    print(
                        f"      ⚠️ ADVERTENCIA: El tipo de entrada '{code}' no existe en Odoo. Créalo en Configuración → Tipos de otras entradas.")

        self.client.execute('hr.payslip', 'compute_sheet', [payslip_id])

    def get_draft_payslips(self):
        return self.client.execute(
            'hr.payslip', 'search',
            [['state', 'in', ['draft', 'verify']]]
        )

    def get_active_contracts(self, date_start, date_end):
        domain = [
            ['state', 'in', ['open', 'close']],
            ['date_start', '<=', date_end],
            '|', ['date_end', '=', False], ['date_end', '>=', date_start]
        ]

        return self.client.execute(
            'hr.contract', 'search_read',
            domain,
            fields=['employee_id', 'structure_type_id', 'date_start']
        )

    def slip_exists(self, employee_id, date_from, date_to):
        count = self.client.execute(
            'hr.payslip', 'search_count',
            [
                ['employee_id', '=', employee_id],
                ['date_from', '=', date_from],
                ['date_to', '=', date_to]
            ]
        )
        return count > 0

    def create_payslip(self, contract_id, employee_id, date_from, date_to):
        print(
            f"      [Repo] Creando borrador para Empleado ID {employee_id}...")

        emp_data = self.client.execute(
            'hr.employee', 'read', [employee_id], fields=['name']
        )[0]
        x_slip_name = f"Nómina {emp_data['name']} ({date_from} - {date_to})"

        vals = {
            'employee_id': employee_id,
            'contract_id': contract_id,
            'date_from': date_from,
            'date_to': date_to,
            'name': x_slip_name,
        }

        slip_id = self.client.execute('hr.payslip', 'create', [vals])

        if isinstance(slip_id, list):
            slip_id = slip_id[0]

        print(f"      [Repo] Nómina creada con ID interno: {slip_id}")

        try:
            self.client.execute('hr.payslip', 'compute_sheet', [slip_id])
        except Exception as e:
            print(f"      [Repo] Advertencia leve en compute_sheet: {e}")

        x_input_definitions = {
            'HED_QTY': 'Horas Extras Diurnas',
            'HEN_QTY': 'Horas Extras Nocturnas',
            'RNOC_QTY': 'Recargo Nocturno',
            'EXT_BASICO': 'Cálculo Básico API',
            'EXT_TRANS': 'Cálculo Transporte API',
            'EXT_HED': 'Cálculo HED API',
            'EXT_HEN': 'Cálculo HEN API',
            'EXT_SALUD': 'Cálculo Salud API',
            'EXT_PENSION': 'Cálculo Pensión API',
        }

        all_codes = list(x_input_definitions.keys())
        existing_types = self.client.execute(
            'hr.payslip.input.type', 'search_read',
            [['code', 'in', all_codes]],
            fields=['id', 'code']
        )
        type_map = {t['code']: t['id'] for t in existing_types}

        for code, name in x_input_definitions.items():
            if code not in type_map:
                new_type_id = self.client.execute(
                    'hr.payslip.input.type', 'create',
                    [{'name': name, 'code': code}]
                )
                if isinstance(new_type_id, list):
                    new_type_id = new_type_id[0]
                type_map[code] = new_type_id
                print(f"      [Repo] Tipo de entrada '{code}' creado en Odoo.")

        existing_inputs = self.client.execute(
            'hr.payslip.input', 'search_read',
            [['payslip_id', '=', slip_id]],
            fields=['code']
        )
        existing_codes = {inp['code'] for inp in existing_inputs}

        for code in all_codes:
            if code not in existing_codes:
                self.client.execute(
                    'hr.payslip.input', 'create',
                    [{'payslip_id': slip_id,
                      'input_type_id': type_map[code],
                      'amount': 0}]
                )
                print(f"      [Repo] Input '{code}' asociado a nómina {slip_id}.")

        return slip_id

    def get_full_data_for_xml(self, payslip_id):
        slip = self.client.execute(
            'hr.payslip', 'read', [payslip_id],
            fields=['number', 'date_from', 'date_to', 'employee_id',
                    'contract_id', 'company_id', 'line_ids']
        )[0]

        company = self.client.execute('res.company', 'read', [slip['company_id'][0]],
                                      fields=['name', 'vat', 'street', 'city', 'state_id', 'phone', 'partner_id'])[0]

        employee = self.client.execute('hr.employee', 'read', [slip['employee_id'][0]],
                                       fields=['name', 'identification_id', 'address_id'])[0]

        employee_address = {}
        if employee.get('address_id'):
            partner_id = employee['address_id'][0]
            employee_address = self.client.execute(
                'res.partner', 'read', [partner_id],
                fields=['street', 'city', 'state_id']
            )[0]

        # Fetch Partner to check Identification Type (needed for robust DV logic)
        partner_id = company['partner_id'][0]
        partner = self.client.execute('res.partner', 'read', [partner_id], fields=[
                                      'l10n_latam_identification_type_id'])[0]

        l10n_co_document_code = ''
        if partner.get('l10n_latam_identification_type_id'):
            # Fetch the code of the identification type
            ident_type = self.client.execute('l10n_latam.identification.type', 'read',
                                             [partner['l10n_latam_identification_type_id'][0]],
                                             fields=['l10n_co_document_code'])[0]
            l10n_co_document_code = ident_type.get('l10n_co_document_code')

        # Logic replicated from Odoo _get_vat_verification_code
        nit = company.get('vat', '')
        dv = ''

        if l10n_co_document_code != 'rut':
            # Not a RUT, usually no DV logic needed or different. Odoo returns ''
            pass
        elif nit and '-' in nit:
            parts = nit.split('-')
            nit = parts[0]
            dv = parts[1]
        elif nit and len(nit) > 1:
            # Fallback: Last digit is assumed to be DV if it's RUT and no hyphen is provided
            dv = nit[-1]
            nit = nit[:-1]

        company['matches_nit'] = nit
        company['matches_dv'] = dv
        contract = self.client.execute('hr.contract', 'read', [slip['contract_id'][0]],
                                       fields=['wage', 'date_start'])[0]

        lines = self.client.execute(
            'hr.payslip.line', 'read', slip['line_ids'],
            fields=['code', 'total', 'quantity', 'rate', 'category_id', 'name']
        )

        return {
            'slip': slip,
            'company': company,
            'employee': employee,
            'employee_address': employee_address,
            'contract': contract,
            'lines': lines,
            'worked_days': []
        }

    def get_dian_configuration(self, company_id):
        # 1. Fetch DIAN Operation Mode
        # We fetch the first operation mode defined for the company, as we don't have a specific 'payroll' type yet.
        # Ideally, there should be a selection or a specific ID in config.
        print(
            f"      [Repo] Buscando configuración DIAN para Company ID {company_id}...")

        op_modes = self.client.execute(
            'l10n_co_dian.operation_mode', 'search_read',
            [['company_id', '=', company_id]],
            fields=['dian_software_id', 'dian_software_security_code',
                    'dian_testing_id', 'dian_software_operation_mode']
        )

        dian_config = {}
        if op_modes:
            # Taking the first one for now, or prefer one if logic needed
            mode = op_modes[0]
            dian_config = {
                'software_id': mode.get('dian_software_id'),
                'software_pin': mode.get('dian_software_security_code'),
                'testing_id': mode.get('dian_testing_id'),
                'operation_mode': mode.get('dian_software_operation_mode')
            }
            print(
                f"      [Repo] Modo de operación encontrado: {mode.get('dian_software_operation_mode')}")
        else:
            print(
                f"      ⚠️ ADVERTENCIA: No se encontró modo de operación DIAN para la compañía.")

        # 2. Fetch Active Certificate
        # We look for a certificate that is active and valid for the company
        print(f"      [Repo] Buscando certificado digital activo...")
        today = datetime.date.today().strftime('%Y-%m-%d')

        # Checking if 'state' field exists to include it in domain, otherwise just dates
        # But to be safe and avoid complex inline logic that breaks picking, let's just query by company and dates first.
        # Most Odoo certificate modules filter by date.

        domain_cert = [
            ['company_id', '=', company_id],
            ['date_start', '<=', today],
            ['date_end', '>=', today]
        ]

        # If we really need to check for 'valid' state, we should check field existence separately or filter in python.
        # But usually date range is enough for active certs.

        certs = self.client.execute(
            'certificate.certificate', 'search_read',
            domain_cert,
            fields=['name', 'serial_number', 'date_start',
                    'date_end', 'pkcs12_password', 'content'],
            limit=1,
            order='date_end desc'
        )

        certs = self.client.execute(
            'certificate.certificate', 'search_read',
            domain_cert,
            fields=['name', 'serial_number', 'date_start', 'date_end',
                    'pkcs12_password', 'content', 'private_key_id', 'public_key_id'],
            limit=1,
            order='date_end desc'
        )

        cert_config = {}
        if certs:
            cert = certs[0]

            # Fetch Key Contents from ir.attachment
            private_key_content = None
            public_key_content = None

            if cert.get('private_key_id'):
                # Handle Many2one tuple (id, name)
                pk_id = cert['private_key_id'][0] if isinstance(
                    cert['private_key_id'], (list, tuple)) else cert['private_key_id']
                att = self.client.execute('ir.attachment', 'read', [
                                          pk_id], fields=['datas'])[0]
                private_key_content = att.get('datas')

            if cert.get('public_key_id'):
                pub_id = cert['public_key_id'][0] if isinstance(
                    cert['public_key_id'], (list, tuple)) else cert['public_key_id']
                att = self.client.execute('ir.attachment', 'read', [
                                          pub_id], fields=['datas'])[0]
                public_key_content = att.get('datas')

            cert_config = {
                'name': cert.get('name'),
                'serial_number': cert.get('serial_number'),
                'start_date': cert.get('date_start'),
                'end_date': cert.get('date_end'),
                'password': cert.get('pkcs12_password'),
                'content': cert.get('content'),  # PKCS12 file (base64)
                'private_key_pem': private_key_content,  # PEM (base64)
                'public_key_pem': public_key_content    # PEM (base64)
            }
            print(
                f"      [Repo DEBUG] Cert Data Found: Name={cert.get('name')}")
            print(
                f"      [Repo DEBUG] Private Key ID: {cert.get('private_key_id')} (Content Len: {len(private_key_content) if private_key_content else 0})")
            print(
                f"      [Repo DEBUG] Public Key ID: {cert.get('public_key_id')} (Content Len: {len(public_key_content) if public_key_content else 0})")
            print(
                f"      [Repo DEBUG] PKCS12 Content Len: {len(cert.get('content') or '')}")
            print(
                f"      [Repo DEBUG] Password Present: {bool(cert.get('pkcs12_password'))}")
            print(
                f"      [Repo] Certificado encontrado: {cert.get('name')} (Expira: {cert.get('date_end')})")
        else:
            print(f"      ⚠️ ADVERTENCIA: No se encontró certificado válido.")

        return {
            'dian': dian_config,
            'certificate': cert_config
        }

    def x_upload_xml_to_odoo(self, payslip_id, filename, xml_content):
        import base64
        print(
            f"      [Repo] Retornando XML a Odoo Documents (ID {payslip_id})...")
        encoded_xml = base64.b64encode(
            xml_content.encode('utf-8')).decode('utf-8')
        doc_vals = {
            'name': filename,
            'type': 'binary',
            'datas': encoded_xml,
            'res_model': 'hr.payslip',
            'res_id': payslip_id,
            'mimetype': 'application/xml',
        }
        try:
            # Buscar si ya existe en documents
            existing_docs = self.client.execute(
                'documents.document', 'search',
                [['res_model', '=', 'hr.payslip'], [
                    'res_id', '=', payslip_id], ['name', '=', filename]]
            )

            if existing_docs:
                doc_id = existing_docs[0]
                self.client.execute('documents.document', 'write', [
                                    doc_id], {'datas': encoded_xml})
                print(
                    f"      [Repo] XML actualizado en Odoo documents, ID: {doc_id}")
                return doc_id
            else:
                doc_id = self.client.execute(
                    'documents.document', 'create', [doc_vals])
                if isinstance(doc_id, list) and len(doc_id) > 0:
                    doc_id = doc_id[0]
                print(
                    f"      [Repo] XML retornado a Odoo documents, ID: {doc_id}")
                return doc_id
        except Exception as e:
            print(
                f"      ⚠️ Error documents.document: {e}. Usando ir.attachment...")

            existing_atts = self.client.execute(
                'ir.attachment', 'search',
                [['res_model', '=', 'hr.payslip'], [
                    'res_id', '=', payslip_id], ['name', '=', filename]]
            )

            if existing_atts:
                attachment_id = existing_atts[0]
                self.client.execute('ir.attachment', 'write', [
                                    attachment_id], {'datas': encoded_xml})
                print(
                    f"      [Repo] XML actualizado en Odoo adjuntos (ir.attachment), ID: {attachment_id}")
                return attachment_id
            else:
                attachment_id = self.client.execute(
                    'ir.attachment', 'create', [doc_vals])
                if isinstance(attachment_id, list) and len(attachment_id) > 0:
                    attachment_id = attachment_id[0]
                print(
                    f"      [Repo] XML retornado a Odoo adjuntos, ID adjunto: {attachment_id}")
                return attachment_id
