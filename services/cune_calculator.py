import hashlib

class x_CuneCalculator:
    @staticmethod
    def x_calculate(data, pin_software="75315"):
        """
        Calcula el CUNE (Código Único de Nómina Electrónica) según el estándar de la DIAN.
        """
        try:
            numero_nomina = str(data['NumeroSecuenciaXML']['Numero'])
            fecha_generacion = str(data['Periodo']['FechaGen'])
            hora_generacion = str(data['InformacionGeneral']['HoraGen'])
            valor_devengado = f"{data['Totales']['DevengadoTotal']:.2f}"
            valor_deducido = f"{data['Totales']['DeduccionesTotal']:.2f}"
            valor_total = f"{data['Totales']['TotalAPagar']:.2f}"
            nit_empleador = str(data['Empleador']['NIT'])
            documento_trabajador = str(data['Trabajador']['Documento'])
            tipo_xml = "102"
            ambiente = str(data['InformacionGeneral']['Ambiente'])

            cune_string = (
                f"{numero_nomina}{fecha_generacion}{hora_generacion}"
                f"{valor_devengado}{valor_deducido}{valor_total}"
                f"{nit_empleador}{documento_trabajador}{tipo_xml}"
                f"{pin_software}{ambiente}"
            )

            return hashlib.sha384(cune_string.encode('utf-8')).hexdigest()

        except KeyError as e:
            raise KeyError(f"Error al calcular CUNE: Falta campo requerido {e}")
