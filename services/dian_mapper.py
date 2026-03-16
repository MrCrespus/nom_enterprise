from config import Config
import datetime


class DianMapper:
    @staticmethod
    def to_dian_structure(data, dian_settings=None, calculations=None, worked_days=None, overtime_hours=None):
        slip = data['slip']
        contract = data['contract']
        employee = data['employee']
        company = data['company']

        dian_settings = dian_settings or {}
        dian_config = dian_settings.get('dian', {})
        calculations = calculations or {}
        worked_days = worked_days or {}
        overtime_hours = overtime_hours or {}

        now = datetime.datetime.now()
        fecha_gen = now.strftime("%Y-%m-%d")
        hora_gen = now.strftime("%H:%M:%S")

        # Forzar siempre a 30 días para el reporte DIAN
        dias_trabajados = 30

        sueldo_basico = calculations.get('EXT_BASICO', 0)
        val_transporte = calculations.get('EXT_TRANS', 0)
        val_hed = calculations.get('EXT_HED', 0)
        val_hen = calculations.get('EXT_HEN', 0)
        val_rnoc = calculations.get('EXT_RNOC', 0)
        val_salud = abs(calculations.get('EXT_SALUD', 0))
        val_pension = abs(calculations.get('EXT_PENSION', 0))
        val_fsp = abs(calculations.get('EXT_FSP', 0))

        horas_hed = float(overtime_hours.get('HED', 0))
        horas_hen = float(overtime_hours.get('HEN', 0))
        horas_rnoc = float(overtime_hours.get('RNOC', 0))

        devengados = {
            "Basico": {"DiasTrabajados": dias_trabajados, "SueldoTrabajado": sueldo_basico},
            "Transporte": [],
            "HEDs": [], "HENs": [], "HRNs": [], "HEDDFs": [], "HENDFs": []
        }

        total_devengado = sueldo_basico

        if val_transporte > 0:
            devengados["Transporte"].append(
                {"AuxilioTransporte": val_transporte, "ViaticoManutAlojS": 0})
            total_devengado += val_transporte

        if val_hed > 0:
            devengados["HEDs"].append({
                "HoraInicio": None, "HoraFin": None,
                "Cantidad": horas_hed,
                "Porcentaje": Config.PORCENTAJES_EXTRA.get('HED', 25.0),
                "Pago": val_hed
            })
            total_devengado += val_hed

        if val_hen > 0:
            devengados["HENs"].append({
                "HoraInicio": None, "HoraFin": None,
                "Cantidad": horas_hen,
                "Porcentaje": Config.PORCENTAJES_EXTRA.get('HEN', 75.0),
                "Pago": val_hen
            })
            total_devengado += val_hen

        if val_rnoc > 0:
            devengados["HRNs"].append({
                "HoraInicio": None, "HoraFin": None,
                "Cantidad": horas_rnoc,
                "Porcentaje": Config.PORCENTAJES_EXTRA.get('HRN', 35.0),
                "Pago": val_rnoc
            })
            total_devengado += val_rnoc

        calc_total_devengado = round(
            sueldo_basico + val_transporte + val_hed + val_hen + val_rnoc, 2)
        calc_total_deducciones = round(val_salud + val_pension + val_fsp, 2)

        # Calcular porcentajes internamente para no crear campos en Odoo
        ratio = contract.get('wage', 0) / 1750905  # SMMLV 2026
        if ratio <= 1.0:
            val_porc_salud = 4.0
        elif ratio <= 3.0:
            val_porc_salud = 10.0
        else:
            val_porc_salud = 12.0

        deducciones = {
            "Salud": {"Porcentaje": val_porc_salud, "Deduccion": val_salud},
            "Pension": {"Porcentaje": 4.0, "Deduccion": val_pension},
            "FondoSolidaridad": []
        }

        if val_fsp > 0:
            deducciones["FondoSolidaridad"].append({
                "DeduccionSP": val_fsp, "DeduccionSub": 0, "Porcentaje": 1.0
            })

        return {
            "Novedad": {"CUNENovedad": "false"},
            "Periodo": {
                "FechaIngreso": contract.get('date_start'),
                "FechaLiquidacionInicio": slip['date_from'],
                "FechaLiquidacionFin": slip['date_to'],
                "TiempoLaborado": dias_trabajados,
                "FechaGen": fecha_gen
            },
            "NumeroSecuenciaXML": {
                "Consecutivo": slip.get('number', ''),
                "Numero": slip.get('number', ''),
                "Prefijo": "NOM"
            },
            "LugarGeneracionXML": {
                "Pais": "CO",
                "DepartamentoEstado": company.get('state_id', [17])[0] if company.get('state_id') else 17,
                "MunicipioCiudad": company.get('city', '17001'),
                "Idioma": "es"
            },
            "ProveedorXML": {
                "RazonSocial": company.get('name', ''),
                "PrimerApellido": "",
                "PrimerNombre": "",
                "NIT": company.get('matches_nit', company.get('vat', '')),
                "DV": company.get('matches_dv', "1"),
                "SoftwareID": dian_config.get('software_id', ''),
                "SoftwareSC": dian_config.get('software_pin', '')
            },
            "InformacionGeneral": {
                "Version": "V1.0: Documento Soporte de Pago de Nómina Electrónica",
                "Ambiente": "2" if dian_config.get('testing_id') else "1",
                "TipoXML": "102",
                "CUNE": "",
                "EncripCUNE": "CUNE-SHA384",
                "FechaGen": fecha_gen,
                "HoraGen": hora_gen,
                "PeriodoNomina": "4",
                "TipoMoneda": "COP"
            },
            "Empleador": {
                "NIT": company.get('matches_nit', company.get('vat', '')),
                "DigitoVerificacion": company.get('matches_dv', "1"),
                "RazonSocial": company.get('name'),
                "Pais": "CO",
                "DepartamentoEstado": "17",
                "MunicipioCiudad": "17001",
                "Direccion": company.get('street')
            },
            "Trabajador": {
                "TipoTrabajador": "01",
                "SubtipoTrabajador": "00",
                "AltoRiesgoPension": False,
                "Documento": employee.get('identification_id'),
                "PrimerApellido": employee.get('name').split(' ')[-1] if employee.get('name') else '',
                "PrimerNombre": employee.get('name').split(' ')[0] if employee.get('name') else '',
                "LugarTrabajoPais": "CO",
                "LugarTrabajoDepartamentoEstado": "17",
                "LugarTrabajoMunicipioCiudad": "17001",
                "Direccion": data.get('employee_address', {}).get('street', 'Sin Dirección'),
                "Sueldo": contract.get('wage'),
                "CodigoTrabajador": employee.get('id')
            },
            "Pago": {
                "Forma": "1",
                "Metodo": "10",
            },
            "FechasPagos": [
                {"FechaPago": slip['date_to']}
            ],
            "Devengados": devengados,
            "Deducciones": deducciones,
            "Totales": {
                "DevengadoTotal": calc_total_devengado,
                "DeduccionesTotal": calc_total_deducciones,
                "TotalAPagar": round(calc_total_devengado - calc_total_deducciones, 2),
                "ComprobanteTotal": round(calc_total_devengado - calc_total_deducciones, 2)
            }
        }
