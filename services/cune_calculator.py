import hashlib

class x_CuneCalculator:
    @staticmethod
    def x_calculate(data, pin_software="75315"):
        """
        Calcula el CUNE/CUID (Código Único de Nómina Electrónica) según el estándar de la DIAN.
        Soporta Nómina Individual (102) y Notas de Ajuste (103).
        """
        try:
            # Determinamos si es Ajuste o Estándar
            is_adjustment = data.get('TipoNota') is not None
            tipo_xml = "103" if is_adjustment else "102"
            
            # Para notas de eliminación (TipoNota 2), los valores son fijos
            is_delete = data.get('TipoNota') == 2
            
            numero = str(data['NumeroSecuenciaXML']['Numero'])
            fecha = str(data['InformacionGeneral']['FechaGen'])
            hora = str(data['InformacionGeneral']['HoraGen'])
            nit_empleador = str(data['Empleador']['NIT'])
            ambiente = str(data['InformacionGeneral']['Ambiente'])
            
            if is_delete:
                v_dev = "0.00"
                v_ded = "0.00"
                v_tol = "0.00"
                doc_trabajador = "0"
            else:
                v_dev = f"{data['Totales']['DevengadoTotal']:.2f}"
                v_ded = f"{data['Totales']['DeduccionesTotal']:.2f}"
                v_tol = f"{data['Totales']['TotalAPagar']:.2f}"
                doc_trabajador = str(data['Trabajador']['NumeroDocumento'])

            cune_string = (
                f"{numero}{fecha}{hora}"
                f"{v_dev}{v_ded}{v_tol}"
                f"{nit_empleador}{doc_trabajador}{tipo_xml}"
                f"{pin_software}{ambiente}"
            )

            return hashlib.sha384(cune_string.encode('utf-8')).hexdigest()

        except KeyError as e:
            raise KeyError(f"Error al calcular CUNE: Falta campo requerido {e}")
