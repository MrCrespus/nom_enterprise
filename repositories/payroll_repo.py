from database.odoo_client import x_OdooClient
import datetime
from logger_config import x_setup_logging

class x_PayrollRepository:
    def __init__(self, client: x_OdooClient):
        self.client = client
        self.logger = x_setup_logging('payroll_repo')

    def x_get_payslip_raw_data(self, payslip_id):
        self.logger.info(f"Obteniendo datos crudos para Payslip ID: {payslip_id}")
        # En v19 no existe 'number', el identificador es solo 'name'
        slip = self.client.x_execute(
            'hr.payslip', 'read', [payslip_id],
            fields=['version_id', 'worked_days_line_ids', 'input_line_ids', 'name', 'employee_id', 'date_to', 'salary_attachment_ids']
        )[0]

        contract = self._x_fetch_contract_data(slip['version_id'][0])
        worked_days = self._x_fetch_worked_days(slip.get('worked_days_line_ids', []))
        manual_inputs = self._x_fetch_manual_inputs(slip.get('input_line_ids', []))
        attachments = self._x_fetch_salary_attachments(slip.get('salary_attachment_ids', []))

        return {
            'payslip_number': slip.get('name'),
            'contract': contract,
            'worked_days': worked_days,
            'manual_inputs': manual_inputs,
            'attachments': attachments
        }

    def _x_fetch_contract_data(self, version_id, employee_id=None):
        """Lee datos del contrato desde hr.version + campos de Studio desde hr.employee.
        
        Los campos x_nivel_riesgo_arl y x_es_independiente fueron añadidos por Studio
        a hr.employee (no a hr.version). Se leen desde el empleado si se provee employee_id.
        """
        # 1. Leer solo los campos nativos de hr.version (siempre disponibles)
        contract = self.client.x_execute(
            'hr.version', 'read', [version_id],
            fields=['wage', 'display_name', 'employee_id']
        )[0]

        # 2. Leer campos de Studio desde hr.employee
        emp_id = employee_id or (contract.get('employee_id') and contract['employee_id'][0])
        if emp_id:
            try:
                emp_data = self.client.x_execute(
                    'hr.employee', 'read', [emp_id],
                    fields=['x_nivel_riesgo_arl', 'x_es_independiente']
                )[0]
                contract['x_nivel_riesgo_arl'] = emp_data.get('x_nivel_riesgo_arl', '1')
                contract['x_es_independiente'] = emp_data.get('x_es_independiente', False)
            except Exception:
                self.logger.warning(f"Campos Studio no disponibles en hr.employee {emp_id}, usando valores por defecto.")
                contract['x_nivel_riesgo_arl'] = '1'
                contract['x_es_independiente'] = False
        else:
            contract['x_nivel_riesgo_arl'] = '1'
            contract['x_es_independiente'] = False

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
        """Busca nóminas en estado 'Validado' o 'Pagado' (v19) que no hayan sido enviadas exitosamente.
        
        En Odoo v19: 'validated' (antes 'done') y 'paid' son los estados finales.
        El campo x_dian_status es de Studio y NO se puede usar en el domain RPC
        (el servidor lo rechaza con ValueError si no está disponible).
        El filtro por x_dian_status se aplica en memoria después de la búsqueda.
        """
        domain = [['state', 'in', ['validated', 'paid']]]

        if date_start and date_end:
            domain.extend([
                ['date_from', '=', date_start],
                ['date_to', '=', date_end]
            ])

        # Obtenemos los campos necesarios — sin x_dian_status en el domain (falla en servidor)
        # Intentamos leer x_dian_status para filtrar en memoria
        try:
            all_slips = self.client.x_execute(
                'hr.payslip', 'search_read', domain,
                fields=['id', 'version_id', 'x_dian_status', 'x_dian_cune', 'is_refund_payslip'], order='id desc'
            )
        except Exception:
            # Si x_dian_status no existe aún en Studio, buscamos sin él
            self.logger.warning("Campo x_dian_status no disponible, procesando sin filtro DIAN.")
            all_slips = self.client.x_execute(
                'hr.payslip', 'search_read', domain,
                fields=['id', 'version_id'], order='id desc'
            )

        # Filtramos en memoria: excluir las ya enviadas
        seen_versions = set()
        unique_ids = []
        for slip in all_slips:
            # Si ya tiene CUNE y NO es un reembolso, la saltamos.
            if slip.get('x_dian_cune') and not slip.get('is_refund_payslip'):
                continue
                
            # Si el estado es 'sent' y no es reembolso, también saltamos
            if slip.get('x_dian_status') == 'sent' and not slip.get('is_refund_payslip'):
                continue

            version_id = slip['version_id'][0] if slip['version_id'] else False
            if version_id and version_id not in seen_versions:
                unique_ids.append(slip['id'])
                seen_versions.add(version_id)
            elif not version_id:
                unique_ids.append(slip['id'])

        self.logger.info(f"Se encontraron {len(unique_ids)} nóminas en estado validado/pagado listas para procesar.")
        return unique_ids

    def x_get_period_from_slip(self, payslip_id):
        self.logger.info(f"Extrayendo período a partir del Payslip ID: {payslip_id}")
        slip = self.client.x_execute(
            'hr.payslip', 'read', [payslip_id], fields=['date_from', 'date_to']
        )
        if slip:
            return slip[0]['date_from'], slip[0]['date_to']
        return None, None


    def x_get_current_reporting_period(self):
        self.logger.info("Buscando el período de nómina configurado en Odoo...")
        
        # 1. Buscamos el último hr.payslip validado/pagado para deducir el período de forma dinámica
        try:
            slip = self.client.x_execute(
                'hr.payslip', 'search_read',
                [['state', 'in', ['validated', 'paid']]], 
                fields=['date_from', 'date_to'], order='write_date desc', limit=1
            )
            if slip:
                self.logger.info(f"Período obtenido dinámicamente de la última nómina modificada/validada: {slip[0]['date_from']} a {slip[0]['date_to']}")
                return slip[0]['date_from'], slip[0]['date_to']
        except Exception as e:
            self.logger.warning(f"Error buscando última nómina validada: {e}")

        # 2. Si no hay nóminas validadas, intentamos buscar en hr.payslip.run (lotes de nómina)
        batch = self.client.x_execute(
            'hr.payslip.run', 'search_read',
            [], fields=['date_start', 'date_end'], order='id desc', limit=1
        )
        if batch:
            self.logger.info(f"Período obtenido de hr.payslip.run: {batch[0]['date_start']} a {batch[0]['date_end']}")
            return batch[0]['date_start'], batch[0]['date_end']

        self.logger.warning("No se encontró ningún período configurado en Odoo. Usando mes actual como fallback.")
        today = datetime.date.today()
        first_day = today.replace(day=1).strftime('%Y-%m-%d')
        import calendar
        last_day = today.replace(day=calendar.monthrange(today.year, today.month)[1]).strftime('%Y-%m-%d')
        return first_day, last_day

    def x_get_active_contracts(self, date_start, date_end):
        """En Odoo v19, los contratos activos se consultan desde hr.employee.

        hr.employee expone los campos de contrato de la versión vigente mediante
        related+inherited desde hr.version:
          - contract_date_start  → version_id.contract_date_start
          - contract_date_end    → version_id.contract_date_end
          - is_in_contract       → version_id.is_in_contract (computed)
          - version_id           → la versión/contrato vigente del empleado
          - wage                 → version_id.wage

        Esto replica el comportamiento de _get_contract_versions() en hr_employee.py:
          - contract_date_start != False  → versión que tiene contrato real
          - solapamiento con el período [date_start, date_end]
        """
        self.logger.info(f"Buscando empleados con contrato activo entre {date_start} y {date_end}")
        domain = [
            ['active', '=', True],
            ['contract_date_start', '!=', False],          # Tiene contrato real (no solo cambio de perfil)
            ['contract_date_start', '<=', date_end],        # Contrato inicia antes del fin del período
            '|',
            ['contract_date_end', '=', False],              # Contrato indefinido
            ['contract_date_end', '>=', date_start]         # O vigente durante el período
        ]
        return self.client.x_execute(
            'hr.employee', 'search_read',
            domain,
            fields=['id', 'name', 'version_id', 'contract_date_start', 'contract_date_end', 'wage', 'structure_type_id']
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

    def x_create_payslip(self, version_id, employee_id, date_from, date_to):
        """En Odoo v19, los payslips se crean con 'version_id' en lugar de 'contract_id'."""
        existing_slips = self.client.x_execute(
            'hr.payslip', 'search',
            [
                ['employee_id', '=', employee_id],
                ['date_from', '=', date_from],
                ['date_to', '=', date_to],
                ['state', '=', 'draft']
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
            'version_id': version_id,
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
        # En v19 no existe 'number', el identificador del payslip es solo 'name'
        slip = self.client.x_execute(
            'hr.payslip', 'read', [payslip_id],
            fields=['name', 'date_from', 'date_to', 'employee_id', 'state', 'is_refund_payslip',
                    'origin_payslip_id', 'version_id', 'company_id', 'line_ids', 'worked_days_line_ids', 
                    'input_line_ids', 'x_dian_cune']
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

        # En Odoo v19, datos del contrato desde hr.version
        # Los campos de Studio (x_nivel_riesgo_arl, x_es_independiente) están en hr.employee
        contract = self.client.x_execute(
            'hr.version', 'read', [slip['version_id'][0]],
            fields=['wage', 'contract_date_start']
        )[0]
        # Enriquecer con campos de Studio desde hr.employee (ya lo tenemos en el slip)
        try:
            emp_studio = self.client.x_execute(
                'hr.employee', 'read', [slip['employee_id'][0]],
                fields=['x_nivel_riesgo_arl', 'x_es_independiente']
            )[0]
            contract['x_nivel_riesgo_arl'] = emp_studio.get('x_nivel_riesgo_arl', '1')
            contract['x_es_independiente'] = emp_studio.get('x_es_independiente', False)
        except Exception:
            self.logger.warning("Campos Studio ARL no disponibles, usando valores por defecto.")
            contract['x_nivel_riesgo_arl'] = '1'
            contract['x_es_independiente'] = False

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

        # En v19, 'number' no existe — normalizamos usando solo 'name'
        slip['number'] = slip.get('name')

        worked_days = self._x_fetch_worked_days(slip.get('worked_days_line_ids', []))
        manual_inputs = self._x_fetch_manual_inputs(slip.get('input_line_ids', []))

        # Recuperar datos del predecesor para Notas de Ajuste
        predecessor = {}
        if slip.get('origin_payslip_id'):
            orig_id = slip['origin_payslip_id'][0]
            try:
                predecessor = self.client.x_execute(
                    'hr.payslip', 'read', [orig_id], 
                    fields=['id', 'name', 'date_from', 'x_dian_cune']
                )[0]
                # Normalizar campo 'number'
                predecessor['number'] = predecessor.get('name')
            except Exception as e:
                self.logger.warning(f"No se pudo obtener datos del predecesor ID {orig_id}: {e}")

        return {
            'slip': slip,
            'company': company,
            'employee': employee,
            'employee_address': employee_address,
            'contract': contract,
            'lines': lines,
            'worked_days': worked_days,
            'manual_inputs': manual_inputs,
            'predecessor': predecessor
        }

    def x_get_dian_configuration(self, company_id):
        self.logger.info(f"Obteniendo configuración DIAN para Company ID: {company_id}")

        # 1. Intentar obtener parámetros de sistema (ir.config_parameter) como prioridad
        system_params = {}
        try:
            param_keys = [
                'dian.software_id', 
                'dian.software_pin', 
                'dian.test_set_id', 
                'dian.operation_mode'
            ]
            self.logger.info("Buscando sobrescrituras en Parámetros del Sistema (ir.config_parameter)...")
            params_data = self.client.x_execute(
                'ir.config_parameter', 'search_read', 
                [['key', 'in', param_keys]], 
                fields=['key', 'value']
            )
            system_params = {p['key']: p['value'] for p in params_data}
        except Exception as e:
            self.logger.warning(f"No se pudieron consultar los parámetros de sistema: {e}")

        # 2. Consultar el modelo estándar l10n_co_dian.operation_mode
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

        # 3. Aplicar prioridad: Parámetros de Sistema > Modelo DIAN
        if system_params.get('dian.software_id'):
            dian_config['software_id'] = system_params['dian.software_id']
        if system_params.get('dian.software_pin'):
            dian_config['software_pin'] = system_params['dian.software_pin']
        if system_params.get('dian.test_set_id'):
            dian_config['testing_id'] = system_params['dian.test_set_id']
        if system_params.get('dian.operation_mode'):
            dian_config['operation_mode'] = system_params['dian.operation_mode']

        if system_params:
            self.logger.info(f"Configuración DIAN cargada con overrides: {list(system_params.keys())}")

        today = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

        domain_cert = [
            ['company_id', '=', company_id],
            ['date_start', '<=', today],
            ['date_end', '>=', today]
        ]

        # En Odoo v19, certificate.certificate tiene estos campos centrales:
        # - content: el certificado en cualquier formato (DER/PKCS12/PEM)
        # - pkcs12_password: contraseña para PKCS12
        # - pem_certificate: CALCULADO automáticamente (solo la parte pública)
        # - private_key_id: Many2one a certificate.key (no ir.attachment)
        # - public_key_id: Many2one a certificate.key (no ir.attachment)
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

            # En Odoo v19, private_key_id y public_key_id apuntan a certificate.key
            # El campo relevante en ese modelo es 'pem_key' (en lugar de 'datas' de ir.attachment)
            if cert.get('private_key_id'):
                pk_id = cert['private_key_id'][0] if isinstance(
                    cert['private_key_id'], (list, tuple)) else cert['private_key_id']
                try:
                    key_rec = self.client.x_execute('certificate.key', 'read', [pk_id], fields=['pem_key'])[0]
                    private_key_content = key_rec.get('pem_key')
                except Exception as e:
                    self.logger.warning(f"No se pudo leer la clave privada de certificate.key: {e}")

            if cert.get('public_key_id'):
                pub_id = cert['public_key_id'][0] if isinstance(
                    cert['public_key_id'], (list, tuple)) else cert['public_key_id']
                try:
                    key_rec = self.client.x_execute('certificate.key', 'read', [pub_id], fields=['pem_key'])[0]
                    public_key_content = key_rec.get('pem_key')
                except Exception as e:
                    self.logger.warning(f"No se pudo leer la clave pública de certificate.key: {e}")

            cert_config = {
                'name': cert.get('name'),
                'serial_number': cert.get('serial_number'),
                'start_date': cert.get('date_start'),
                'end_date': cert.get('date_end'),
                'password': cert.get('pkcs12_password'),
                'content': cert.get('content'),    # PKCS12/DER/PEM binario original
                'private_key_pem': private_key_content,  # PEM de la clave privada (desde certificate.key)
                'public_key_pem': public_key_content     # PEM de la clave pública (desde certificate.key)
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
        try:
            # En Odoo 19 usamos message_post con subtipo para asegurar renderizado HTML
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

    def x_update_dian_fields(self, payslip_id, status, zip_key=None, cune=None):
        """Actualiza el estado DIAN, ZipKey y CUNE en los campos de Studio de Odoo"""
        self.logger.info(f"Actualizando estado DIAN '{status}' para Payslip ID: {payslip_id}")
        
        vals = {'x_dian_status': status}
        if zip_key:
            vals['x_dian_zipkey'] = zip_key
        if cune:
            vals['x_dian_cune'] = cune

        # Intentar escribir todos los campos; si alguno falla (Studio), reintentar solo con el status
        try:
            self.client.x_execute('hr.payslip', 'write', [payslip_id], vals)
        except Exception as e:
            self.logger.warning(f"Error escribiendo campos extendidos DIAN (posiblemente no existan en Studio): {e}")
            self.client.x_execute('hr.payslip', 'write', [payslip_id], {'x_dian_status': status})
