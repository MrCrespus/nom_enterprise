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
                fields=['wage', 'display_name', 'x_nivel_riesgo_arl']
            )[0]
        except Exception:
            self.logger.warning(f"No se encontró nivel de riesgo ARL para contrato {contract_id}, usando nivel 1.")
            contract = self.client.x_execute('hr.contract', 'read', [contract_id], fields=['wage', 'display_name'])[0]
            contract['x_nivel_riesgo_arl'] = 1
            return contract

    def _x_fetch_worked_days(self, line_ids):
        if not line_ids: return {}
        lines = self.client.x_execute('hr.payslip.worked_days', 'read', line_ids, fields=['code', 'number_of_days'])
        return {item['code']: item['number_of_days'] for item in lines}

    def _x_fetch_manual_inputs(self, line_ids):
        if not line_ids: return {}
        lines = self.client.x_execute('hr.payslip.input', 'read', line_ids, fields=['code', 'amount'])
        return {item['code'].replace('_QTY', ''): item['amount'] for item in lines}

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


    def _x_ensure_dian_attachment(self, employee_id, company_id, code, amount):
        MAPEO_ATTACHMENTS = {
            'EXT_SINDICATO': ('Sindicatos', 'Sindicato'),
            'EXT_COOPERATIVA': ('Cooperativas', 'Cooperativa'),
            'EXT_PENSION_VOL': ('PensionVoluntaria', 'Pensión Voluntaria'),
            'EXT_AFC': ('AFC', 'Ahorro al Fomento a la Construcción (AFC)'),
            'EXT_PLAN_COMP': ('PlanesComplementarios', 'Plan Complementario de Salud'),
            'EXT_EDUCACION': ('Educacion', 'Educación'),
            'EXT_DEUDA': ('Deuda', 'Deuda/Libranza'),
            'EXT_ANTICIPO': ('Anticipos', 'Anticipo'),
            'EXT_SANCION': ('Sanciones', 'Sanción'),
            'EXT_RETEN_FUENTE': ('RetencionFuente', 'Retención en la Fuente')
        }
        
        if code not in MAPEO_ATTACHMENTS:
            return False
            
        dian_type, desc = MAPEO_ATTACHMENTS[code]
        valor = float(amount)
        if valor <= 0:
            return True
            
        types = self.client.x_execute('hr.payslip.input.type', 'search_read', 
            [['code', '=', 'ATTACH_SALARY']], fields=['id'])
        if not types:
            types = self.client.x_execute('hr.payslip.input.type', 'search_read', 
                [['available_in_attachments', '=', True]], fields=['id'], limit=1)
        type_id = types[0]['id'] if types else False
        
        desc_full = f"{desc} (Auto API)"
        domain = [
            ['employee_ids', 'in', [employee_id]],
            ['state', '=', 'open'],
            ['description', '=', desc_full]
        ]
        exists = self.client.x_execute('hr.salary.attachment', 'search', domain)
        
        if exists:
            self.logger.info(f"Actualizando attachment existente {desc_full} para empleado {employee_id}")
            self.client.x_execute('hr.salary.attachment', 'write', exists[0], {
                'monthly_amount': valor,
                'total_amount': valor
            })
        else:
            self.logger.info(f"Creando nuevo attachment {desc_full} para empleado {employee_id} con valor {valor}")
            vals = {
                'employee_ids': [(6, 0, [employee_id])],
                'company_id': company_id,
                'description': desc_full,
                'other_input_type_id': type_id,
                'monthly_amount': valor,
                'total_amount': valor,
                'state': 'open',
            }
            try:
                self.client.x_execute('hr.salary.attachment', 'create', [vals])
                self.logger.info(f"Attachment {desc_full} creado con éxito.")
            except Exception as e:
                self.logger.error(f"Fallo al crear el attachment {desc_full}: {e}")
        return True

    def x_write_calculations(self, payslip_id, calculated_values):
        self.logger.info(f"Inyectando cálculos en Payslip ID: {payslip_id}")
        CODIGOS_DIAS = {k for k in calculated_values if k.endswith('_DIAS')}

        CODIGOS_DEDUCCION = {
            'EXT_SALUD', 'EXT_PENSION', 'EXT_ARL', 'EXT_FSP',
            'EXT_RETEN_FUENTE', 'EXT_PENSION_VOL', 'EXT_AFC',
            'EXT_SINDICATO', 'EXT_COOPERATIVA', 'EXT_PLAN_COMP',
            'EXT_EDUCACION', 'EXT_DEUDA', 'EXT_ANTICIPO', 'EXT_SANCION',
        }

        contrato_data = self.client.x_execute(
            'hr.payslip', 'read', [payslip_id], fields=['contract_id', 'employee_id', 'company_id']
        )[0]
        contract_id = contrato_data['contract_id'][0]
        employee_id = contrato_data['employee_id'][0]
        company_id = contrato_data.get('company_id', [False])[0]

        self.logger.info(f"x_write_calculations recibió los siguientes valores a procesar: {calculated_values}")

        # Primero, procesamos las deducciones que son embargos (attachments)
        for ext_code, amount in list(calculated_values.items()):
            if self._x_ensure_dian_attachment(employee_id, company_id, ext_code, amount):
                # Si es un attachment válido, lo removemos de calculated_values 
                # para que no se re-inyecte como una simple línea de payslip abajo
                del calculated_values[ext_code]

        # Una vez creados los attachments, ejecutamos compute_sheet para que Odoo los incluya en el payslip nativamente
        self.client.x_execute('hr.payslip', 'compute_sheet', [payslip_id])

        x_input_to_rule = {
            'EXT_BASICO': ['BASIC', 'SUELDO'],
            'EXT_TRANS': ['AUX_TRANS'],
            'EXT_HED': ['HED'],
            'EXT_HEN': ['HEN'],
            'EXT_RNOC': ['RNOC', 'REC_NOC'],
            'EXT_HED_DF': ['HED_DF'],
            'EXT_HEN_DF': ['HEN_DF'],
            'EXT_RNOC_DF': ['RNOC_DF'],
            'EXT_PRIMA': ['PRIMA'],
            'EXT_CESANTIAS': ['CESANTIAS'],
            'EXT_INT_CES': ['INT_CES'],
            'EXT_VAC_COM': ['VAC_COM', 'SAL_VAC'],
            'EXT_VAC_COMP_DIN': ['VAC_COMP'],
            'EXT_DOTACION': ['DOTACION'],
            'EXT_BONIF_SAL': ['BONIF_SAL'],
            'EXT_BONIF_NSAL': ['BONIF_NSAL'],
            'EXT_COMISION': ['COMISION'],
            'EXT_AUX_SAL': ['AUX_SAL'],
            'EXT_AUX_NSAL': ['AUX_NSAL'],
            'EXT_INCAP_EC': ['INCAP_EC', 'INCAP_C'],
            'EXT_INCAP_AT': ['INCAP_AT', 'INCAP_ARL'],
            'EXT_LIC_REM': ['LIC_REM'],
            'EXT_SALUD': ['SALUD', 'SS_SALUD'],
            'EXT_PENSION': ['PENSION', 'SS_PENSION'],
            'EXT_ARL': ['ARL'],
            'EXT_FSP': ['FSP'],
        }

        payslip_lines = self.client.x_execute(
            'hr.payslip.line', 'search_read',
            [['slip_id', '=', payslip_id]],
            fields=['id', 'code', 'salary_rule_id', 'contract_id', 'employee_id']
        )
        line_code_map = {ln['code']: ln for ln in payslip_lines}

        ded_category = self.client.x_execute(
            'hr.salary.rule.category', 'search',
            [['code', '=', 'DED']], limit=1
        )
        alw_category = self.client.x_execute(
            'hr.salary.rule.category', 'search',
            [['code', '=', 'ALW']], limit=1
        )

        for ext_code, amount in calculated_values.items():
            if ext_code in CODIGOS_DIAS:
                continue

            valor = float(amount)
            if valor == 0:
                continue

            rule_codes = x_input_to_rule.get(ext_code, [])
            es_deduccion = ext_code in CODIGOS_DEDUCCION
            valor_linea = -abs(valor) if es_deduccion else abs(valor)

            actualizado = False
            for rule_code in rule_codes:
                if rule_code in line_code_map:
                    ln = line_code_map[rule_code]
                    self.logger.debug(f"Actualizando regla {rule_code} con valor {valor_linea}")
                    self.client.x_execute(
                        'hr.payslip.line', 'write',
                        [ln['id']],
                        {'amount': valor_linea}
                    )
                    actualizado = True
                    break

            if not actualizado and rule_codes and valor != 0:
                categoria = ded_category[0] if (es_deduccion and ded_category) else (alw_category[0] if alw_category else False)
                if categoria:
                    regla = self.client.x_execute(
                        'hr.salary.rule', 'search',
                        [['code', '=', rule_codes[0]]], limit=1
                    )
                    if regla:
                        self.logger.debug(f"Creando nueva línea para regla {rule_codes[0]} con valor {valor_linea}")
                        nueva_linea = {
                            'slip_id': payslip_id,
                            'salary_rule_id': regla[0],
                            'contract_id': contract_id,
                            'employee_id': employee_id,
                            'amount': valor_linea,
                            'quantity': 1.0,
                            'rate': 100.0,
                        }
                        try:
                            self.client.x_execute('hr.payslip.line', 'create', [nueva_linea])
                        except Exception:
                            self.logger.error(f"Error al crear línea para regla {rule_codes[0]}")

    def x_get_draft_payslips(self):
        drafts = self.client.x_execute(
            'hr.payslip', 'search',
            [['state', 'in', ['draft', 'verify']]]
        )
        self.logger.info(f"Se encontraron {len(drafts)} nóminas en estado draft/verify.")
        return drafts

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
                    'contract_id', 'company_id', 'line_ids']
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
                fields=['wage', 'date_start', 'x_nivel_riesgo_arl'])[0]
        except Exception:
            contract = self.client.x_execute(
                'hr.contract', 'read', [slip['contract_id'][0]],
                fields=['wage', 'date_start'])[0]
            contract['x_nivel_riesgo_arl'] = 1

        lines = self.client.x_execute(
            'hr.payslip.line', 'read', slip['line_ids'],
            fields=['code', 'total', 'quantity', 'rate', 'category_id', 'name']
        )

        slip['number'] = slip.get('number') or slip.get('name')

        return {
            'slip': slip,
            'company': company,
            'employee': employee,
            'employee_address': employee_address,
            'contract': contract,
            'lines': lines,
            'worked_days': []
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
        doc_vals = {
            'name': filename,
            'type': 'binary',
            'datas': encoded_xml,
            'res_model': 'hr.payslip',
            'res_id': payslip_id,
            'mimetype': 'application/xml',
        }
        try:
            hr_folder = self.client.x_execute('documents.document', 'search', [
                ['name', '=', 'HR'], ['type', '=', 'folder']
            ], limit=1)
            if hr_folder:
                doc_vals['folder_id'] = hr_folder[0]

            existing_docs = self.client.x_execute(
                'documents.document', 'search',
                [['res_model', '=', 'hr.payslip'], [
                    'res_id', '=', payslip_id], ['name', '=', filename]]
            )

            if existing_docs:
                doc_id = existing_docs[0]
                self.client.x_execute('documents.document', 'write', [
                                    doc_id], {'datas': encoded_xml})
                self.logger.info(f"Documento actualizado en Odoo (Documents): {doc_id}")
                return doc_id
            else:
                doc_id = self.client.x_execute(
                    'documents.document', 'create', [doc_vals])
                if isinstance(doc_id, list) and len(doc_id) > 0:
                    doc_id = doc_id[0]
                self.logger.info(f"Documento creado en Odoo (Documents): {doc_id}")
                return doc_id
        except Exception:
            self.logger.warning("Fallo al subir a documents.document, intentando ir.attachment.")
            existing_atts = self.client.x_execute(
                'ir.attachment', 'search',
                [['res_model', '=', 'hr.payslip'], [
                    'res_id', '=', payslip_id], ['name', '=', filename]]
            )

            if existing_atts:
                attachment_id = existing_atts[0]
                self.client.x_execute('ir.attachment', 'write', [
                                    attachment_id], {'datas': encoded_xml})
                self.logger.info(f"Adjunto actualizado en Odoo (ir.attachment): {attachment_id}")
                return attachment_id
            else:
                attachment_id = self.client.x_execute(
                    'ir.attachment', 'create', [doc_vals])
                if isinstance(attachment_id, list) and len(attachment_id) > 0:
                    attachment_id = attachment_id[0]
                self.logger.info(f"Adjunto creado en Odoo (ir.attachment): {attachment_id}")
                return attachment_id
