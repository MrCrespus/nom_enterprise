from config import Config
import datetime


class DianMapper:
    @staticmethod
    def to_dian_structure(data, dian_settings=None):
        slip = data['slip']
        lines = data['lines']
        contract = data['contract']
        employee = data['employee']
        company = data['company']

        # Use provided settings or fallback to empty dict
        dian_settings = dian_settings or {}
        dian_config = dian_settings.get('dian', {})

        now = datetime.datetime.now()
        fecha_gen = now.strftime("%Y-%m-%d")
        hora_gen = now.strftime("%H:%M:%S")

        devengados = {
            "Basico": {"DiasTrabajados": 30, "SueldoTrabajado": 0},
            "Transporte": [],
            "HEDs": [], "HENs": [], "HRNs": [], "HEDDFs": [], "HENDFs": []
        }

        deducciones = {
            "Salud": {"Porcentaje": 4.0, "Deduccion": 0},
            "Pension": {"Porcentaje": 4.0, "Deduccion": 0},
            "FondoSolidaridad": []
        }

        total_devengado = 0
        total_deducciones = 0

        for line in lines:
            odoo_code = line['code']
            valor = line['total']

            dian_type = Config.CONCEPTO_MAP.get(odoo_code)
            if not dian_type:
                continue

            if dian_type == 'Basico':
                dias = line.get('quantity', 30)
                devengados["Basico"]["DiasTrabajados"] = int(
                    dias) if dias > 0 else 30
                devengados["Basico"]["SueldoTrabajado"] += valor
                total_devengado += valor

            elif dian_type == 'Transporte':
                devengados["Transporte"].append(
                    {"AuxilioTransporte": valor, "ViaticoManutAlojS": 0})
                total_devengado += valor

            elif dian_type in ['HED', 'HEN', 'HRN', 'HEDDF', 'HENDF']:
                porcentaje = Config.PORCENTAJES_EXTRA.get(dian_type, 0.0)
                horas = line.get('quantity', 0)

                key_plural = dian_type + "s"
                devengados[key_plural].append({
                    "HoraInicio": None, "HoraFin": None,
                    "Cantidad": horas, "Porcentaje": porcentaje, "Pago": valor
                })
                total_devengado += valor

            elif dian_type == 'Salud':
                deducciones["Salud"]["Deduccion"] += abs(valor)
                total_deducciones += abs(valor)

            elif dian_type == 'Pension':
                deducciones["Pension"]["Deduccion"] += abs(valor)
                total_deducciones += abs(valor)

            elif dian_type == 'FondoSolidaridad':
                deducciones["FondoSolidaridad"].append({
                    "DeduccionSP": abs(valor), "DeduccionSub": 0, "Porcentaje": 1.0
                })
                total_deducciones += abs(valor)

        return {
            "Novedad": {"CUNENovedad": "false"},
            "Periodo": {
                "FechaIngreso": contract.get('date_start'),
                "FechaLiquidacionInicio": slip['date_from'],
                "FechaLiquidacionFin": slip['date_to'],
                "TiempoLaborado": devengados["Basico"]["DiasTrabajados"],
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
                # Self-provider logic implies Company is Provider
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
                # 2=Test, 1=Production
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
                "DevengadoTotal": total_devengado,
                "DeduccionesTotal": total_deducciones,
                "TotalAPagar": total_devengado - total_deducciones,
                "ComprobanteTotal": total_devengado - total_deducciones
            }
        }
