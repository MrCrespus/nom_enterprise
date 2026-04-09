from config import Config
import datetime
import hashlib

class x_DianMapper:
    @classmethod
    def x_to_dian_structure(cls, data: dict, dian_settings=None, calculations=None, overtime_hours=None, attachments=None):
        slip = data['slip']
        contract = data['contract']
        employee = data['employee']
        company = data['company']

        dian_settings = dian_settings or {}
        overtime_hours = overtime_hours or {}
        attachments = attachments or []

        if not calculations:
            calculations = cls._x_extract_calculations_from_lines(data.get('lines', []))
        
        calculations = calculations or {}
        worked_days = data.get('worked_days', {})

        devengados, total_devengado = cls._x_map_devengados(calculations, overtime_hours, worked_days)
        deducciones, total_deducciones = cls._x_map_deducciones(calculations, contract)
        
        devengados, deducciones, total_devengado, total_deducciones = cls._x_process_attachments(
            attachments, devengados, deducciones, total_devengado, total_deducciones
        )

        return cls._x_assemble_final_dict(
            data, dian_settings, devengados, deducciones, 
            total_devengado, total_deducciones
        )

    @classmethod
    def _x_map_devengados(cls, calculations, overtime_hours, worked_days):
        sueldo_basico = calculations.get('BASIC', 0)
        
        # Filtramos ausencias que afectan el básico según la guía (LEAVE100, LEAVE90, LEAVE110, LEAVE120, OUT)
        dias_ausente = sum(float(worked_days.get(code, {}).get('number_of_days', 0) or 0) for code in ['LEAVE100', 'LEAVE90', 'LEAVE110', 'LEAVE120', 'OUT'])
        dias_basico = max(30 - int(dias_ausente), 0)

        devengados = {
            "Basico": {"DiasTrabajados": dias_basico, "SueldoTrabajado": sueldo_basico},
            "Transporte": [],
            "HEDs": [], "HENs": [], "HRNs": [],
            "HEDDFs": [], "HRDDFs": [], "HENDFs": [], "HRNDFs": [],
            "Vacaciones": {"VacacionesComunes": [], "VacacionesCompensadas": []},
            "Incapacidades": [],
            "Bonificaciones": [], "OtrosConceptos": [], "Comisiones": []
        }
        total = sueldo_basico

        if calculations.get('AUX_TRANS', 0) > 0:
            devengados["Transporte"].append({"AuxilioTransporte": calculations['AUX_TRANS']})
            total += calculations['AUX_TRANS']

        total += cls._x_add_overtime_sections(devengados, calculations, overtime_hours, worked_days)
        total += cls._x_add_additional_earnings(devengados, calculations, worked_days)

        return devengados, total

    @classmethod
    def _x_add_overtime_sections(cls, devengados, calculations, overtime_hours, worked_days):
        sections = {
            'HED': ('HEDs', 'Pago', 25.0),
            'HEN': ('HENs', 'Pago', 75.0),
            'RN': ('HRNs', 'Pago', 35.0),
            'HEDD': ('HEDDFs', 'Pago', 100.0),
            'HEND': ('HENDFs', 'Pago', 150.0),
            'RDF': ('HRDDFs', 'Pago', 75.0), # Fixed to HRDDFs based on XSD typical structures. Wait, RDF is Recargo Dominical Festivo (HRDDFs = Hora Recargo Diurno Dominical Festivo).
            'RNDF': ('HRNDFs', 'Pago', 110.0) 
        }
        subtotal = 0.0
        for code, (key, value_key, default_pct) in sections.items():
            if code in calculations:
                val = calculations.get(code, 0)
                if val > 0:
                    inpt = overtime_hours.get(code, {})
                    wd = worked_days.get(code, {})
                    hours = float(inpt.get('amount', 0) or wd.get('number_of_hours', 0) or wd.get('number_of_days', 0) or 0)
                    
                    devengados[key].append({
                        "Cantidad": hours,
                        "Porcentaje": Config.PORCENTAJES_EXTRA.get(code, default_pct),
                        value_key: val
                    })
                    subtotal += val
        return subtotal

    @classmethod
    def _x_add_additional_earnings(cls, devengados, calculations, worked_days):
        subtotal = 0.0
        
        if 'COMIS' in calculations and calculations['COMIS'] > 0:
            devengados['Comisiones'].append({"Comision": calculations['COMIS']})
            subtotal += calculations['COMIS']

        if 'LEAVE120' in calculations and calculations['LEAVE120'] > 0:
            dias_vac = int(worked_days.get('LEAVE120', {}).get('number_of_days', 15) or 15)
            devengados["Vacaciones"]["VacacionesComunes"].append({"Cantidad": dias_vac, "Pago": calculations['LEAVE120']})
            subtotal += calculations['LEAVE120']

        if 'LEAVE110' in calculations and calculations['LEAVE110'] > 0:
            dias_incap = int(worked_days.get('LEAVE110', {}).get('number_of_days', 0) or 0)
            devengados["Incapacidades"].append({"Cantidad": dias_incap, "Pago": calculations['LEAVE110'], "Tipo": "1"})
            subtotal += calculations['LEAVE110']

        if 'REIMBURSEMENT' in calculations and calculations['REIMBURSEMENT'] > 0:
            devengados["OtrosConceptos"].append({"DescripcionConcepto": "Reembolso", "ConceptoNS": calculations['REIMBURSEMENT']})
            subtotal += calculations['REIMBURSEMENT']

        return subtotal

    @classmethod
    def _x_map_deducciones(cls, calculations, contract):
        val_salud = abs(calculations.get('SALUD', 0))
        val_pension = abs(calculations.get('EMP_PENSION', 0))

        deducciones = {
            "Salud": {"Porcentaje": 4.0, "Deduccion": val_salud},
            "Pension": {"Porcentaje": 4.0, "Deduccion": val_pension},
            "FondoSolidaridad": [], "RetencionFuente": [], "PensionVoluntaria": [],
            "AFC": [], "Sindicatos": [], "Cooperativas": [], "PlanesComplementarios": [],
            "Educacion": [], "Deuda": [], "Anticipos": [], "Sanciones": [], "OtrasDeducciones": [],
            "Libranzas": [], "PagosTerceros": [], "EmbargoFiscal": [], "Reintegros": []
        }
        total = val_salud + val_pension
        
        # Mapeo de embargos y deducciones genéricas según la guía
        for code in ['EMB_ALI', 'EMB_GEN', 'ATTACH_SALARY', 'ASSIG_SALARY', 'DEDUCTION']:
            if code in calculations:
                val = abs(calculations[code])
                if val > 0:
                    deducciones["OtrasDeducciones"].append({"OtraDeduccion": val})
                    total += val
                
        return deducciones, total

    @classmethod
    def _x_process_attachments(cls, attachments, devengados, deducciones, total_dev, total_ded):
        for att in attachments:
            amount = float(att.get('active_amount', 0) or att.get('monthly_amount', 0) or 0)
            if amount <= 0: continue
            
            if att.get('is_refund'):
                cat, inner_key = cls.x_map_attachment_to_devengo_category(att)
                if cat == 'OtrosConceptos':
                    devengados[cat].append({"DescripcionConcepto": att.get('description', 'Anticipo/Reintegro')[:100], inner_key: amount})
                elif cat == 'Transporte':
                    devengados[cat].append({inner_key: amount})
                else:
                    devengados[cat].append({inner_key: amount})
                total_dev += amount
            else:
                cat = cls.x_map_attachment_to_dian_category(att)
                cls._x_add_attachment_to_deducciones(deducciones, cat, amount)
                total_ded += amount
        return devengados, deducciones, total_dev, total_ded

    @classmethod
    def x_map_attachment_to_devengo_category(cls, attachment):
        code = (attachment.get('input_type_code') or '').upper()
        desc = (attachment.get('description') or '').upper()
        
        if 'BONO' in code or 'BONIFICACION' in desc:
            return 'Bonificaciones', 'BonificacionNS' if 'NS' in code or 'NO SALARIAL' in desc else 'BonificacionS'
        if 'COMIS' in code or 'COMISION' in desc:
            return 'Comisiones', 'Comision'
        if 'VIATIC' in code or 'VIATICO' in desc:
            return 'Transporte', 'ViaticoManuAlojNS' if 'NS' in code or 'NO SALARIAL' in desc else 'ViaticoManuAlojS'
        if 'PRIMA' in code or 'PRIMA EXTR' in desc:
            # Primas is single-occurrence in XSD; we should map to OtrosConceptos if Primas already used, 
            # but for XSD adherence let's default to OtrosConceptos for these custom attachments to be safe
            return 'OtrosConceptos', 'ConceptoNS'
            
        return 'OtrosConceptos', 'ConceptoS'

    @classmethod
    def _x_add_attachment_to_deducciones(cls, deducciones, category, amount):
        if category in deducciones and isinstance(deducciones[category], list):
            if category == 'Sindicatos':
                deducciones[category].append({"Porcentaje": 0.0, "Deduccion": amount})
            elif category == 'Sanciones':
                # El XSD usa SancionPublic, SancionPriv. Por pragmatismo asignamos a privados por defecto.
                deducciones[category].append({"SancionPublic": 0.0, "SancionPriv": amount})
            elif category == 'Libranzas':
                deducciones[category].append({"Descripcion": "Libranza", "Deduccion": amount})
            elif category == 'Cooperativas':
                deducciones[category].append({"Cooperativa": amount})
            elif category == 'PensionVoluntaria':
                deducciones[category].append({"PensionVoluntaria": amount})
            elif category == 'AFC':
                deducciones[category].append({"AFC": amount})
            elif category == 'PlanesComplementarios':
                deducciones[category].append({"PlanComplementario": amount})
            elif category == 'Educacion':
                deducciones[category].append({"Educacion": amount})
            elif category == 'Deuda':
                deducciones[category].append({"Deuda": amount})
            elif category == 'PagosTerceros':
                deducciones[category].append({"PagoTercero": amount})
            elif category == 'Anticipos':
                deducciones[category].append({"Anticipo": amount})
            elif category == 'RetencionFuente':
                deducciones[category].append({"RetencionFuente": amount})
            elif category == 'EmbargoFiscal':
                deducciones[category].append({"EmbargoFiscal": amount})
            elif category == 'Reintegros':
                deducciones[category].append({"Reintegro": amount})
            else:
                deducciones["OtrasDeducciones"].append({"OtraDeduccion": amount})
        else:
            deducciones["OtrasDeducciones"].append({"OtraDeduccion": amount})

    @classmethod
    def _x_assemble_final_dict(cls, data, dian_settings, devengados, deducciones, total_dev, total_ded):
        slip, contract, employee, company = data['slip'], data['contract'], data['employee'], data['company']
        dian_config = dian_settings.get('dian', {})
        now = datetime.datetime.now()
        
        tipo_documento = employee.get('l10n_co_document_type') or '13' # CC
        try: tipo_documento = str(int(tipo_documento))
        except: tipo_documento = '13'
        
        nombres = employee.get('name', '').split(' ')
        primer_nombre = nombres[0] if nombres else ''
        otros_nombres = ' '.join(nombres[1:-2]) if len(nombres) > 2 else ''
        primer_apellido = nombres[-2] if len(nombres) >= 2 else (nombres[-1] if nombres else '')
        segundo_apellido = nombres[-1] if len(nombres) >= 2 else ''
        
        software_id = dian_config.get('software_id', '')
        software_pin = dian_config.get('software_pin', '')
        # En v19, 'number' no existe en hr.payslip — se usa 'name' que ya fue normalizado en el repo
        numero_slip = slip.get('number') or slip.get('name', '')
        software_sc_str = f"{software_id}{software_pin}{numero_slip}"
        software_sc_hash = hashlib.sha384(software_sc_str.encode('utf-8')).hexdigest()
        
        return {
            "Novedad": {"CUNENov": "false"},
            "Periodo": {
                # En Odoo v19, hr.version usa 'contract_date_start' (antes 'date_start' en hr.contract)
                "FechaIngreso": contract.get('contract_date_start') or contract.get('date_start'), 
                "FechaLiquidacionInicio": slip['date_from'], 
                "FechaLiquidacionFin": slip['date_to'], 
                "TiempoLaborado": 30, 
                "FechaGen": now.strftime("%Y-%m-%d")
            },
            "NumeroSecuenciaXML": {"Consecutivo": slip.get('number') or slip.get('name', ''), "Numero": slip.get('number') or slip.get('name', ''), "Prefijo": "NOM"},
            "LugarGeneracionXML": {"Pais": "CO", "DepartamentoEstado": "17", "MunicipioCiudad": "17001", "Idioma": "es"},
            "ProveedorXML": {"RazonSocial": company.get('name', ''), "NIT": company.get('matches_nit', company.get('vat', '123456789')), "DV": company.get('matches_dv', "1"), "SoftwareID": software_id, "SoftwareSC": software_sc_hash},
            "InformacionGeneral": {"Version": "V1.0: Documento Soporte de Pago de Nómina Electrónica", "Ambiente": "2" if dian_config.get('testing_id') else "1", "TipoXML": "102", "CUNE": "", "EncripCUNE": "CUNE-SHA384", "FechaGen": now.strftime("%Y-%m-%d"), "HoraGen": now.strftime("%H:%M:%S"), "PeriodoNomina": "4", "TipoMoneda": "COP"},
            "Empleador": {"NIT": company.get('matches_nit', company.get('vat', '123456789')), "DigitoVerificacion": company.get('matches_dv', "1"), "RazonSocial": company.get('name'), "Pais": "CO", "DepartamentoEstado": "17", "MunicipioCiudad": "17001", "Direccion": company.get('street', 'Sin Direccion')},
            "Trabajador": {
                "TipoTrabajador": "01", 
                "SubTipoTrabajador": "00", 
                "AltoRiesgoPension": False, 
                "TipoDocumento": tipo_documento,
                "NumeroDocumento": employee.get('identification_id', '123456'), 
                "PrimerApellido": primer_apellido, 
                "SegundoApellido": segundo_apellido,
                "PrimerNombre": primer_nombre, 
                "OtrosNombres": otros_nombres,
                "LugarTrabajoPais": "CO", 
                "LugarTrabajoDepartamentoEstado": "17", 
                "LugarTrabajoMunicipioCiudad": "17001", 
                "LugarTrabajoDireccion": data.get('employee_address', {}).get('street', 'Sin Dirección'), 
                "SalarioIntegral": False,
                "TipoContrato": "1",
                "Sueldo": contract.get('wage', 0), 
                "CodigoTrabajador": employee.get('id', '')
            },
            "Pago": {"Forma": "1", "Metodo": "10"},
            "FechasPagos": [{"FechaPago": slip['date_to']}],
            "Devengados": devengados,
            "Deducciones": deducciones,
            "Totales": {
                "DevengadoTotal": round(total_dev, 2),
                "DeduccionesTotal": round(total_ded, 2),
                "TotalAPagar": round(total_dev - total_ded, 2),
                "ComprobanteTotal": round(total_dev - total_ded, 2)
            }
        }

    @classmethod
    def x_map_attachment_to_dian_category(cls, attachment):
        code = (attachment.get('input_type_code') or '').upper()
        desc = (attachment.get('description') or '').upper()
        if 'SIND' in code or 'SINDICATO' in desc: return 'Sindicatos'
        if 'COOP' in code or 'COOPERATIVA' in desc: return 'Cooperativas'
        if 'ANT' in code or 'ANTICIPO' in desc: return 'Anticipos'
        if 'LIBRAN' in code or 'LIBRANZA' in desc: return 'Libranzas'
        if 'DEUD' in code or 'DEUDA' in desc: return 'Deuda'
        if 'SANC' in code or 'SANCION' in desc: return 'Sanciones'
        if 'EDUC' in code or 'EDUCACION' in desc: return 'Educacion'
        if 'AFC' in code or 'AFC' in desc: return 'AFC'
        if 'PENS' in code or 'VOLUNTARIA' in desc: return 'PensionVoluntaria'
        if 'PLAN_COMP' in code or 'COMPLEMENTARIO' in desc: return 'PlanesComplementarios'
        if 'EMB' in code or 'EMBARGO' in desc: return 'EmbargoFiscal'
        if 'REIN' in code or 'REINTEGRO' in desc: return 'Reintegros'
        return 'OtrasDeducciones'

    @classmethod
    def _x_extract_calculations_from_lines(cls, lines):
        # Whitelist estricta basada en la Guía de Parametrización
        WHITELIST = [
            'BASIC', 'AUX_TRANS', 'HED', 'HEN', 'HEDD', 'HEND', 'RN', 'RDF', 'RNDF',
            'SALUD', 'EMP_PENSION', 'LEAVE110', 'LEAVE120', 'EMB_ALI', 'EMB_GEN', 
            'COMIS', 'ATTACH_SALARY', 'ASSIG_SALARY', 'DEDUCTION', 'REIMBURSEMENT'
        ]
        
        extracted = {}
        for line in lines:
            code = line.get('code')
            if code not in WHITELIST:
                continue
                
            total = abs(line.get('total', 0))
            if total > 0:
                extracted[code] = total
        
        return extracted
