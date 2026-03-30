from config import Config
import datetime


class x_DianMapper:
    @classmethod
    def x_to_dian_structure(cls, data: dict, dian_settings=None, calculations=None, overtime_hours=None, attachments=None):
        slip = data['slip']
        contract = data['contract']
        employee = data['employee']
        company = data['company']

        dian_settings = dian_settings or {}
        calculations = calculations or {}
        overtime_hours = overtime_hours or {}
        attachments = attachments or []

        # 1. Preparar Devengados
        devengados, total_devengado = cls._x_map_devengados(calculations, overtime_hours)
        
        # 2. Preparar Deducciones base
        deducciones, total_deducciones = cls._x_map_deducciones(calculations, contract)
        
        # 3. Procesar Adjuntos (Earnings and Deductions)
        devengados, deducciones, total_devengado, total_deducciones = cls._x_process_attachments(
            attachments, devengados, deducciones, total_devengado, total_deducciones
        )

        # 4. Ensamblar Estructura Final
        return cls._x_assemble_final_dict(
            data, dian_settings, devengados, deducciones, 
            total_devengado, total_deducciones
        )

    @classmethod
    def _x_map_devengados(cls, calculations, overtime_hours):
        sueldo_basico = calculations.get('EXT_BASICO', 0)
        devengados = {
            "Basico": {"DiasTrabajados": 30, "SueldoTrabajado": sueldo_basico},
            "Transporte": [],
            "HEDs": [], "HENs": [], "HRNs": [],
            "HEDDFs": [], "HENDFs": [], "HRNDFs": [],
            "Vacaciones": [], "Primas": [], "Cesantias": [],
            "Incapacidades": [], "Licencias": [], "Bonificaciones": [],
            "Auxilios": [], "OtrosConceptos": [], "Comisiones": [],
            "Dotacion": [], "ApoyoSost": [], "Reintegros": [],
        }
        total = sueldo_basico

        # Transporte
        val_trans = calculations.get('EXT_TRANS', 0)
        if val_trans > 0:
            devengados["Transporte"].append({"AuxilioTransporte": val_trans, "ViaticoManutAlojS": 0})
            total += val_trans

        # Horas Extra
        total += cls._x_add_overtime_sections(devengados, calculations, overtime_hours)
        
        # Otros Devengos (Vacaciones, Primas, etc.)
        total += cls._x_add_additional_earnings(devengados, calculations)

        return devengados, total

    @classmethod
    def _x_add_overtime_sections(cls, devengados, calculations, overtime_hours):
        sections = {
            'HED': ('HEDs', 'Pago', 25.0),
            'HEN': ('HENs', 'Pago', 75.0),
            'RNOC': ('HRNs', 'Pago', 35.0),
            'HED_DF': ('HEDDFs', 'Pago', 100.0),
            'HEN_DF': ('HENDFs', 'Pago', 150.0),
            'RNOC_DF': ('HRNDFs', 'Pago', 75.0)
        }
        subtotal = 0.0
        for code, (key, value_key, default_pct) in sections.items():
            val = calculations.get(f'EXT_{code}', 0)
            if val > 0:
                hours = float(overtime_hours.get(code, 0))
                devengados[key].append({
                    "HoraInicio": None, "HoraFin": None,
                    "Cantidad": hours,
                    "Porcentaje": Config.PORCENTAJES_EXTRA.get(code, default_pct),
                    value_key: val
                })
                subtotal += val
        return subtotal

    @classmethod
    def _x_add_additional_earnings(cls, devengados, calculations):
        subtotal = 0.0
        # Simplificando para brevedad del refactor, se mantiene la lógica original
        mappings = [
            ('EXT_PRIMA', 'Primas', lambda v: {"Cantidad": 30, "Pago": v, "PagoNS": 0}),
            ('EXT_CESANTIAS', 'Cesantias', lambda v: {"Pago": v, "PagoNS": 0, "Porcentaje": 12.0, "InteresesCesantias": calculations.get('EXT_INT_CES') or None}),
            ('EXT_DOTACION', 'Dotacion', lambda v: {"Dotacion": v}),
            ('EXT_COMISION', 'Comisiones', lambda v: {"Comision": v}),
            ('EXT_APOYO_SOST', 'ApoyoSost', lambda v: {"ApoyoSost": v}),
        ]
        
        for ext_key, section_key, formatter in mappings:
            val = calculations.get(ext_key, 0)
            if val > 0:
                devengados[section_key].append(formatter(val))
                subtotal += val
                if ext_key == 'EXT_CESANTIAS':
                    subtotal += calculations.get('EXT_INT_CES', 0)

        # Vacaciones, Incapacidades, Licencias (Especiales)
        subtotal += cls._x_add_special_earnings(devengados, calculations)
        return subtotal

    @classmethod
    def _x_add_special_earnings(cls, devengados, calculations):
        subtotal = 0.0
        # Vacaciones
        if calculations.get('EXT_VAC_COM', 0) > 0:
            devengados["Vacaciones"].append({"tipo": "comunes", "Cantidad": int(calculations.get('EXT_VAC_COM_DIAS', 0)), "Pago": calculations['EXT_VAC_COM']})
            subtotal += calculations['EXT_VAC_COM']
        
        # Bonificaciones, Auxilios, Otros (Salarial/No Salarial)
        for prefix, key in [('EXT_BONIF', 'Bonificaciones'), ('EXT_AUX', 'Auxilios'), ('EXT_OTRO', 'OtrosConceptos')]:
            for suffix, label in [('_SAL', 'salarial'), ('_NSAL', 'no_salarial')]:
                val = calculations.get(f'{prefix}{suffix}', 0)
                if val > 0:
                    if key == 'OtrosConceptos':
                        devengados[key].append({"tipo": label, "DescripcionConcepto": f"Otro concepto {label}", "ConceptoS": val if suffix == '_SAL' else 0, "ConceptoNS": val if suffix == '_NSAL' else 0})
                    else:
                        devengados[key].append({"tipo": label, f"{key[:-2]}on{label.replace('no_', 'No').capitalize()}": val})
                    subtotal += val
        return subtotal

    @classmethod
    def _x_map_deducciones(cls, calculations, contract):
        val_salud = abs(calculations.get('EXT_SALUD', 0))
        val_pension = abs(calculations.get('EXT_PENSION', 0))
        
        wage = contract.get('wage', 0)
        ratio = wage / 1750905
        porc_salud = 4.0 if ratio <= 1.0 else (10.0 if ratio <= 3.0 else 12.0)

        deducciones = {
            "Salud": {"Porcentaje": porc_salud, "Deduccion": val_salud},
            "Pension": {"Porcentaje": 4.0, "Deduccion": val_pension},
            "FondoSolidaridad": [], "RetencionFuente": [], "PensionVoluntaria": [],
            "AFC": [], "Sindicatos": [], "Cooperativas": [], "PlanesComplementarios": [],
            "Educacion": [], "Deuda": [], "Anticipos": [], "Sanciones": [], "OtrasDeducciones": []
        }
        total = val_salud + val_pension
        
        # Fondo Solidaridad
        val_fsp = abs(calculations.get('EXT_FSP', 0))
        if val_fsp > 0:
            deducciones["FondoSolidaridad"].append({"DeduccionSP": val_fsp, "DeduccionSub": 0, "Porcentaje": 1.0})
            total += val_fsp
            
        # Otras deducciones base
        for ext, key, inner in [('EXT_ARL', 'OtrasDeducciones', 'OtraDeduccion'), ('EXT_RETEN_FUENTE', 'RetencionFuente', 'RetencionFuente')]:
            val = abs(calculations.get(ext, 0))
            if val > 0:
                deducciones[key].append({inner: val})
                total += val
                
        return deducciones, total

    @classmethod
    def _x_process_attachments(cls, attachments, devengados, deducciones, total_dev, total_ded):
        for att in attachments:
            amount = float(att.get('active_amount', 0) or att.get('monthly_amount', 0) or 0)
            if amount <= 0: continue
            
            if att.get('is_refund'):
                devengados["OtrosConceptos"].append({'Concepto': att.get('description', 'Otro Concepto'), 'Valor': amount, 'Tipo': 'OtrosConceptos'})
                total_dev += amount
            else:
                cat = cls.x_map_attachment_to_dian_category(att)
                cls._x_add_attachment_to_deducciones(deducciones, cat, amount)
                total_ded += amount
        return devengados, deducciones, total_dev, total_ded

    @classmethod
    def _x_add_attachment_to_deducciones(cls, deducciones, category, amount):
        # Even if we have specific categories, the current template only supports OtrasDeducciones
        # for these extra items. So we map them all to OtrasDeducciones for now 
        # to ensure they are visible in the XML.
        if category in ['Sindicatos', 'Cooperativas', 'Anticipos', 'Deuda', 'Sanciones', 'Educacion', 'AFC', 'PensionVoluntaria', 'OtrasDeducciones']:
            deducciones["OtrasDeducciones"].append({"OtraDeduccion": amount})
        else:
            deducciones["OtrasDeducciones"].append({"OtraDeduccion": amount})

    @classmethod
    def _x_assemble_final_dict(cls, data, dian_settings, devengados, deducciones, total_dev, total_ded):
        slip, contract, employee, company = data['slip'], data['contract'], data['employee'], data['company']
        dian_config = dian_settings.get('dian', {})
        now = datetime.datetime.now()
        
        return {
            "Novedad": {"CUNENovedad": "false"},
            "Periodo": {"FechaIngreso": contract.get('date_start'), "FechaLiquidacionInicio": slip['date_from'], "FechaLiquidacionFin": slip['date_to'], "TiempoLaborado": 30, "FechaGen": now.strftime("%Y-%m-%d")},
            "NumeroSecuenciaXML": {"Consecutivo": slip.get('number', ''), "Numero": slip.get('number', ''), "Prefijo": "NOM"},
            "LugarGeneracionXML": {"Pais": "CO", "DepartamentoEstado": company.get('state_id', [17])[0], "MunicipioCiudad": company.get('city', '17001'), "Idioma": "es"},
            "ProveedorXML": {"RazonSocial": company.get('name', ''), "NIT": company.get('matches_nit', company.get('vat', '')), "DV": company.get('matches_dv', "1"), "SoftwareID": dian_config.get('software_id', ''), "SoftwareSC": dian_config.get('software_pin', '')},
            "InformacionGeneral": {"Version": "V1.0: Documento Soporte de Pago de Nómina Electrónica", "Ambiente": "2" if dian_config.get('testing_id') else "1", "TipoXML": "102", "CUNE": "", "EncripCUNE": "CUNE-SHA384", "FechaGen": now.strftime("%Y-%m-%d"), "HoraGen": now.strftime("%H:%M:%S"), "PeriodoNomina": "4", "TipoMoneda": "COP"},
            "Empleador": {"NIT": company.get('matches_nit', company.get('vat', '')), "DigitoVerificacion": company.get('matches_dv', "1"), "RazonSocial": company.get('name'), "Pais": "CO", "DepartamentoEstado": "17", "MunicipioCiudad": "17001", "Direccion": company.get('street')},
            "Trabajador": {"TipoTrabajador": "01", "SubtipoTrabajador": "00", "AltoRiesgoPension": False, "Documento": employee.get('identification_id'), "PrimerApellido": employee.get('name', '').split(' ')[-1], "PrimerNombre": employee.get('name', '').split(' ')[0], "LugarTrabajoPais": "CO", "LugarTrabajoDepartamentoEstado": "17", "LugarTrabajoMunicipioCiudad": "17001", "Direccion": data.get('employee_address', {}).get('street', 'Sin Dirección'), "Sueldo": contract.get('wage'), "CodigoTrabajador": employee.get('id')},
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
        
        if 'SIND' in code or 'SINDICATO' in desc:
            return 'Sindicatos'
        if 'COOP' in code or 'COOPERATIVA' in desc:
            return 'Cooperativas'
        if 'ANT' in code or 'ANTICIPO' in desc:
            return 'Anticipos'
        if 'DEUD' in code or 'LIBRANZA' in desc or 'DEUDA' in desc:
            return 'Deuda'
        if 'SANC' in code or 'SANCION' in desc:
            return 'Sanciones'
        if 'EDUC' in code or 'EDUCACION' in desc:
            return 'Educacion'
        if 'AFC' in code or 'AFC' in desc:
            return 'AFC'
        if 'PENS' in code or 'VOLUNTARIA' in desc:
            return 'PensionVoluntaria'
            
        return 'OtrasDeducciones'
