from database.odoo_client import x_OdooClient
import datetime
from logger_config import x_setup_logging

class x_PayrollRepository:
    def __init__(self, client: x_OdooClient):
        self.client = client
        self.logger = x_setup_logging('payroll_repo')

    def x_get_payslip_raw_data(self, payslip_id):
        self.logger.info(f"Obteniendo datos crudos para Payslip ID: {payslip_id}")
        slip = self.client.x_execute(
            'hr.payslip', 'read', [payslip_id],
            fields=['contract_id', 'worked_days_line_ids', 'input_line_ids', 'number', 'name', 'employee_id', 'date_to', 'salary_attachment_ids']
        )[0]

        contract = self._x_fetch_contract_data(slip['contract_id'][0])
        worked_days = self._x_fetch_worked_days(slip.get('worked_days_line_ids', []))
        manual_inputs = self._x_fetch_manual_inputs(slip.get('input_line_ids', []))
        attachments = self._x_fetch_salary_attachments(slip.get('salary_attachment_ids', []))

        return {
            'payslip_number': slip.get('number') or slip.get('name'),
            'contract': contract,
            'worked_days': worked_days,
            'manual_inputs': manual_inputs,
            'attachments': attachments
        }

    def _x_fetch_contract_data(self, contract_id):
        try:
            return self.client.x_execute(
                'hr.contract', 'read', [contract_id],
                fields=['wage', 'display_name', 'x_nivel_riesgo_arl', 'x_es_independiente']
            )[0]
        except Exception:
            self.logger.warning(f"No se encontró nivel de riesgo ARL para contrato {contract_id}, usando nivel 1.")
            contract = self.client.x_execute('hr.contract', 'read', [contract_id], fields=['wage', 'display_name', 'x_es_independiente'])[0]
            contract['x_nivel_riesgo_arl'] = 1
            return contract

    def _x_fetch_worked_days(self, line_ids):
        if not line_ids: return {}
        lines = self.client.x_execute('hr.payslip.worked_days', 'read', line_ids, fields=['code', 'number_of_days', 'number_of_hours', 'amount'])
        return {item['code']: item for item in lines}

    def _x_fetch_manual_inputs(self, line_ids):
        if not line_ids: return {}
        lines = self.client.x_execute('hr.payslip.input', 'read', line_ids, fields=['code', 'amount'])
        return {item['code']: item for item in lines}

    def _x_fetch_salary_attachments(self, attachment_ids):
        if not attachment_ids: return []
        raw_attachments = self.client.x_execute(
            'hr.salary.attachment', 'read', attachment_ids,
            fields=['description', 'monthly_amount', 'active_amount', 'is_refund', 'other_input_type_id']
        )
        for att in raw_attachments:
            if att.get('other_input_type_id'):
                input_type_id = att['other_input_type_id'][0]
                input_type = self.client.x_execute('hr.payslip.input.type', 'read', [input_type_id], fields=['code'])[0]
                att['input_type_code'] = input_type.get('code')
        self.logger.info(f"Se encontraron {len(raw_attachments)} adjuntos de salario.")
        return raw_attachments



    def x_get_slips_for_dian(self, date_start=None, date_end=None):
        """Busca nóminas en estado 'Hecho' (done) que no hayan sido enviadas exitosamente"""
        # Buscamos solo nóminas en estado 'done' (Hecho / Confirmado)
        domain = [['state', '=', 'done']]
        
        # Opcional: Filtrar por las que NO tengan estado 'sent' (exitoso)
        try:
            # Intentamos incluir el filtro de Studio si existe
            domain.append(['x_dian_status', '!=', 'sent'])
        except Exception:
            self.logger.warning("Campo x_dian_status no disponible para filtrado en búsqueda, se filtrará en memoria.")

        if date_start and date_end:
            domain.extend([
                ['date_from', '=', date_start],
                ['date_to', '=', date_end]
            ])
        
        # Obtenemos los campos necesarios para agrupar por contrato
        all_slips = self.client.x_execute(
            'hr.payslip', 'search_read', domain,
            fields=['id', 'contract_id', 'x_dian_status'], order='id desc'
        )
        
        # Filtramos para quedarnos solo con la última nómina (id más alto) por cada contrato
        seen_contracts = set()
        unique_ids = []
        for slip in all_slips:
            # Doble check de seguridad por si el filtro x_dian_status falló en el servidor
            if slip.get('x_dian_status') == 'sent':
                continue

            contract_id = slip['contract_id'][0] if slip['contract_id'] else False
            if contract_id and contract_id not in seen_contracts:
                unique_ids.append(slip['id'])
                seen_contracts.add(contract_id)
            elif not contract_id:
                unique_ids.append(slip['id'])

        self.logger.info(f"Se encontraron {len(unique_ids)} nóminas en estado 'Hecho' listas para procesar.")
        return unique_ids

    def x_get_current_reporting_period(self):
        self.logger.info("Buscando el período de nómina configurado en Odoo...")
        # Intentamos buscar primero en hr.payslip.run (lotes de nómina)
        batch = self.client.x_execute(
            'hr.payslip.run', 'search_read',
            [], fields=['date_start', 'date_end'], order='id desc', limit=1
        )
        if batch:
            self.logger.info(f"Período obtenido de hr.payslip.run: {batch[0]['date_start']} a {batch[0]['date_end']}")
            return batch[0]['date_start'], batch[0]['date_end']

        # Si no hay lotes, buscamos en hr.payslip directamente
        slip = self.client.x_execute(
            'hr.payslip', 'search_read',
            [], fields=['date_from', 'date_to'], order='id desc', limit=1
        )
        if slip:
            self.logger.info(f"Período obtenido de hr.payslip: {slip[0]['date_from']} a {slip[0]['date_to']}")
            return slip[0]['date_from'], slip[0]['date_to']

        self.logger.warning("No se encontró ningún período configurado en Odoo. Usando mes actual como fallback.")
        today = datetime.date.today()
        first_day = today.replace(day=1).strftime('%Y-%m-%d')
        import calendar
        last_day = today.replace(day=calendar.monthrange(today.year, today.month)[1]).strftime('%Y-%m-%d')
        return first_day, last_day

    def x_get_active_contracts(self, date_start, date_end):
        self.logger.info(f"Buscando contratos activos entre {date_start} y {date_end}")
        domain = [
            ['state', 'in', ['open', 'close']],
            ['date_start', '<=', date_end],
            '|', ['date_end', '=', False], ['date_end', '>=', date_start]
        ]
        return self.client.x_execute(
            'hr.contract', 'search_read',
            domain,
            fields=['employee_id', 'structure_type_id', 'date_start']
        )

    def x_slip_exists(self, employee_id, date_from, date_to):
        count = self.client.x_execute(
            'hr.payslip', 'search_count',
            [
                ['employee_id', '=', employee_id],
                ['date_from', '=', date_from],
                ['date_to', '=', date_to]
            ]
        )
        return count > 0

    def x_create_payslip(self, contract_id, employee_id, date_from, date_to):
        existing_slips = self.client.x_execute(
            'hr.payslip', 'search',
            [
                ['employee_id', '=', employee_id],
                ['date_from', '=', date_from],
                ['date_to', '=', date_to],
                ['state', 'in', ['draft', 'verify']]
            ],
            order='id desc',
            limit=1
        )
        if existing_slips:
            slip_id = existing_slips[0]
            self.logger.info(f"Usando Payslip existente ID: {slip_id} para el empleado {employee_id}.")
            return slip_id

        emp_data = self.client.x_execute(
            'hr.employee', 'read', [employee_id], fields=['name']
        )[0]
        x_slip_name = f"Nómina {emp_data['name']} ({date_from} - {date_to})"

        self.logger.info(f"Creando nuevo Payslip: {x_slip_name}")
        vals = {
            'employee_id': employee_id,
            'contract_id': contract_id,
            'date_from': date_from,
            'date_to': date_to,
            'name': x_slip_name,
        }

        slip_id = self.client.x_execute('hr.payslip', 'create', [vals])

        if isinstance(slip_id, list):
            slip_id = slip_id[0]

        try:
            self.client.x_execute('hr.payslip', 'compute_sheet', [slip_id])
        except Exception:
            self.logger.error(f"Error al ejecutar compute_sheet para Payslip ID: {slip_id}")

        return slip_id

    def x_get_full_data_for_xml(self, payslip_id):
        self.logger.info(f"Obteniendo datos completos para XML del Payslip ID: {payslip_id}")
        slip = self.client.x_execute(
            'hr.payslip', 'read', [payslip_id],
            fields=['number', 'name', 'date_from', 'date_to', 'employee_id',
                    'contract_id', 'company_id', 'line_ids', 'worked_days_line_ids', 'input_line_ids']
        )[0]

        company = self.client.x_execute('res.company', 'read', [slip['company_id'][0]],
                                      fields=['name', 'vat', 'street', 'city', 'state_id', 'phone', 'partner_id'])[0]

        employee = self.client.x_execute('hr.employee', 'read', [slip['employee_id'][0]],
                                       fields=['name', 'identification_id', 'address_id'])[0]

        employee_address = {}
        if employee.get('address_id'):
            partner_id = employee['address_id'][0]
            employee_address = self.client.x_execute(
                'res.partner', 'read', [partner_id],
                fields=['street', 'city', 'state_id']
            )[0]

        partner_id = company['partner_id'][0]
        partner = self.client.x_execute('res.partner', 'read', [partner_id], fields=[
                                      'l10n_latam_identification_type_id'])[0]

        l10n_co_document_code = ''
        if partner.get('l10n_latam_identification_type_id'):
            ident_type = self.client.x_execute('l10n_latam.identification.type', 'read',
                                             [partner['l10n_latam_identification_type_id'][0]],
                                             fields=['l10n_co_document_code'])[0]
            l10n_co_document_code = ident_type.get('l10n_co_document_code')

        nit = company.get('vat', '')
        dv = ''

        if l10n_co_document_code != 'rut':
            pass
        elif nit and '-' in nit:
            parts = nit.split('-')
            nit = parts[0]
            dv = parts[1]
        elif nit and len(nit) > 1:
            dv = nit[-1]
            nit = nit[:-1]

        company['matches_nit'] = nit
        company['matches_dv'] = dv
        try:
            contract = self.client.x_execute(
                'hr.contract', 'read', [slip['contract_id'][0]],
                fields=['wage', 'date_start', 'x_nivel_riesgo_arl', 'x_es_independiente'])[0]
        except Exception:
            contract = self.client.x_execute(
                'hr.contract', 'read', [slip['contract_id'][0]],
                fields=['wage', 'date_start', 'x_es_independiente'])[0]
            contract['x_nivel_riesgo_arl'] = 1

        lines = self.client.x_execute(
            'hr.payslip.line', 'read', slip['line_ids'],
            fields=['code', 'total', 'quantity', 'rate', 'category_id', 'name']
        )
        
        category_ids = list({ln['category_id'][0] for ln in lines if ln.get('category_id')})
        if category_ids:
            categories = self.client.x_execute('hr.salary.rule.category', 'read', category_ids, fields=['code'])
            cat_map = {c['id']: c['code'] for c in categories}
            for ln in lines:
                if ln.get('category_id'):
                    ln['category_code'] = cat_map.get(ln['category_id'][0])

        slip['number'] = slip.get('number') or slip.get('name')

        worked_days = self._x_fetch_worked_days(slip.get('worked_days_line_ids', []))
        manual_inputs = self._x_fetch_manual_inputs(slip.get('input_line_ids', []))

        return {
            'slip': slip,
            'company': company,
            'employee': employee,
            'employee_address': employee_address,
            'contract': contract,
            'lines': lines,
            'worked_days': worked_days,
            'manual_inputs': manual_inputs
        }

    def x_get_dian_configuration(self, company_id):
        self.logger.info(f"Obteniendo configuración DIAN para Company ID: {company_id}")
        op_modes = self.client.x_execute(
            'l10n_co_dian.operation_mode', 'search_read',
            [['company_id', '=', company_id]],
            fields=['dian_software_id', 'dian_software_security_code',
                    'dian_testing_id', 'dian_software_operation_mode']
        )

        dian_config = {}
        if op_modes:
            mode = op_modes[0]
            dian_config = {
                'software_id': mode.get('dian_software_id'),
                'software_pin': mode.get('dian_software_security_code'),
                'testing_id': mode.get('dian_testing_id'),
                'operation_mode': mode.get('dian_software_operation_mode')
            }

        today = datetime.date.today().strftime('%Y-%m-%d')

        domain_cert = [
            ['company_id', '=', company_id],
            ['date_start', '<=', today],
            ['date_end', '>=', today]
        ]

        certs = self.client.x_execute(
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
            self.logger.info(f"Certificado encontrado: {cert['name']}")

            private_key_content = None
            public_key_content = None

            if cert.get('private_key_id'):
                pk_id = cert['private_key_id'][0] if isinstance(
                    cert['private_key_id'], (list, tuple)) else cert['private_key_id']
                att = self.client.x_execute('ir.attachment', 'read', [
                                          pk_id], fields=['datas'])[0]
                private_key_content = att.get('datas')

            if cert.get('public_key_id'):
                pub_id = cert['public_key_id'][0] if isinstance(
                    cert['public_key_id'], (list, tuple)) else cert['public_key_id']
                att = self.client.x_execute('ir.attachment', 'read', [
                                          pub_id], fields=['datas'])[0]
                public_key_content = att.get('datas')

            cert_config = {
                'name': cert.get('name'),
                'serial_number': cert.get('serial_number'),
                'start_date': cert.get('date_start'),
                'end_date': cert.get('date_end'),
                'password': cert.get('pkcs12_password'),
                'content': cert.get('content'),  
                'private_key_pem': private_key_content,  
                'public_key_pem': public_key_content    
            }

        return {
            'dian': dian_config,
            'certificate': cert_config
        }

    def x_upload_xml_to_odoo(self, payslip_id, filename, xml_content):
        self.logger.info(f"Subiendo XML {filename} para Payslip ID: {payslip_id}")
        import base64
        encoded_xml = base64.b64encode(
            xml_content.encode('utf-8')).decode('utf-8')
        
        vals = {
            'name': filename,
            'type': 'binary',
            'datas': encoded_xml,
            'res_model': 'hr.payslip',
            'res_id': payslip_id,
            'mimetype': 'application/xml',
        }
        
        # Buscamos si ya existe el adjunto para actualizarlo o crearlo
        existing_atts = self.client.x_execute(
            'ir.attachment', 'search',
            [['res_model', '=', 'hr.payslip'], ['res_id', '=', payslip_id], ['name', '=', filename]]
        )

        if existing_atts:
            attachment_id = existing_atts[0]
            self.client.x_execute('ir.attachment', 'write', [attachment_id], {'datas': encoded_xml})
            self.logger.info(f"Adjunto actualizado en Odoo (ir.attachment): {attachment_id}")
            return attachment_id
        else:
            attachment_id = self.client.x_execute('ir.attachment', 'create', [vals])
            if isinstance(attachment_id, list) and len(attachment_id) > 0:
                attachment_id = attachment_id[0]
            self.logger.info(f"Adjunto creado en Odoo (ir.attachment): {attachment_id}")
            return attachment_id

    def x_post_message(self, res_model, res_id, body, attachment_ids=None):
        self.logger.info(f"Publicando nota en {res_model} ID {res_id}...")
        vals = {
            'body': body,
            'model': res_model,
            'res_id': res_id,
            'attachment_ids': [(6, 0, attachment_ids)] if attachment_ids else []
        }
        try:
            # En Odoo 17/18 usamos message_post con subtipo para asegurar renderizado HTML
            self.client.x_execute(
                res_model, 
                'message_post', 
                [res_id], 
                body=body, 
                attachment_ids=attachment_ids or [],
                message_type='comment',
                subtype_xmlid='mail.mt_note'
            )
            self.logger.info("Nota publicada exitosamente.")
        except Exception as e:
            self.logger.error(f"Error al publicar nota: {e}")

    def x_update_dian_fields(self, payslip_id, status, zip_key=None):
        """Actualiza el estado DIAN y el ZipKey en los campos de Studio de Odoo"""
        self.logger.info(f"Actualizando estado DIAN '{status}' para Payslip ID: {payslip_id}")
        vals = {'x_dian_status': status}
        
        # Guardamos el ZipKey si el campo existe y se proporciona
        if zip_key:
            try:
                # El campo x_dian_zipkey es opcional en Studio
                self.client.x_execute('hr.payslip', 'write', [payslip_id], {
                    'x_dian_status': status,
                    'x_dian_zipkey': zip_key
                })
            except Exception:
                # Si x_dian_zipkey no existe, solo actualizamos el status
                self.client.x_execute('hr.payslip', 'write', [payslip_id], {'x_dian_status': status})
        else:
            self.client.x_execute('hr.payslip', 'write', [payslip_id], {'x_dian_status': status})
