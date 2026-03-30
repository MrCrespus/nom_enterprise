from logger_config import x_setup_logging

class x_ColombiaPayrollEngine:
    SMMLV = 1750905
    AUX_TRANSPORTE = 249095
    UVT = 53000

    PORCENTAJE_ARL = {
        1: 0.00522,
        2: 0.01044,
        3: 0.02436,
        4: 0.04350,
        5: 0.06960,
    }

    def __init__(self):
        self.logger = x_setup_logging('payroll_engine')

    def x_get_arl_rate(self, nivel_riesgo: int) -> float:
        return x_ColombiaPayrollEngine.PORCENTAJE_ARL.get(nivel_riesgo, 0.00522)

    def x_calcular_porcentaje_salud(self, wage: float) -> float:
        ratio = wage / x_ColombiaPayrollEngine.SMMLV
        if ratio <= 1.0:
            return 0.04
        elif ratio <= 3.0:
            return 0.10
        return 0.12

    def x_calculate_payroll(self, contract: dict, worked_days_data: dict, overtime_hours: dict) -> dict:
        self.logger.info(f"Iniciando cálculo de nómina para contrato: {contract.get('display_name')}")
        
        wage = contract.get('wage', 0)
        nivel_riesgo = int(contract.get('x_nivel_riesgo_arl', 1))
        days_worked = self._x_get_days_worked(worked_days_data)

        # 1. Devengos básicos
        basic_data = self._x_calculate_basic_and_transport(wage, days_worked)
        
        # 2. Horas extra
        overtime_data = self._x_calculate_overtime(wage, overtime_hours)
        
        # 3. IBC y Deducciones de Ley
        ibc = self._x_calculate_ibc(basic_data['pago_basico'], overtime_data['total_extras'])
        statutory_deductions = self._x_calculate_statutory_deductions(ibc, wage, nivel_riesgo)
        
        # 4. Entradas adicionales (Primas, vacaciones, etc.)
        additional_inputs = self._x_extract_additional_inputs(overtime_hours)
        
        # 5. Cálculo Neto Final
        val_reten_fuente = float(overtime_hours.get('EXT_RETEN_FUENTE', 0))
        net_summary = self._x_calculate_total_net(
            basic_data, overtime_data, statutory_deductions, 
            additional_inputs, val_reten_fuente
        )

        # 6. Extraer embargos y deducciones manuales
        attachment_codes = [
            'EXT_SINDICATO', 'EXT_COOPERATIVA', 'EXT_PENSION_VOL', 
            'EXT_AFC', 'EXT_PLAN_COMP', 'EXT_EDUCACION', 
            'EXT_DEUDA', 'EXT_ANTICIPO', 'EXT_SANCION'
        ]
        attachment_inputs = {c: float(overtime_hours.get(c, 0)) for c in attachment_codes if c in overtime_hours}
        self.logger.info(f"Inputs manuales detectados para Embargos/Deducciones: {attachment_inputs}")

        # Build final response
        result = {
            'EXT_BASICO': round(basic_data['pago_basico'], 2),
            'EXT_TRANS': round(basic_data['pago_transporte'], 2),
            **{k: round(v, 2) for k, v in overtime_data['items'].items()},
            **{k: v if k.endswith('_DIAS') else round(v, 2) for k, v in additional_inputs.items()},
            **attachment_inputs,
            'EXT_SALUD': round(statutory_deductions['salud'], 2),
            'EXT_PENSION': round(statutory_deductions['pension'], 2),
            'EXT_ARL': round(statutory_deductions['arl'], 2),
            'EXT_FSP': round(statutory_deductions['fsp'], 2),
            'EXT_RETEN_FUENTE': round(val_reten_fuente, 2),
        }
        return result

    def _x_get_days_worked(self, worked_days_data: dict) -> float:
        return float(worked_days_data.get('WORK100', 30))

    def _x_calculate_basic_and_transport(self, wage: float, days_worked: float) -> dict:
        pago_basico = (wage / 30) * days_worked
        pago_transporte = 0.0
        if wage <= (x_ColombiaPayrollEngine.SMMLV * 2) and days_worked > 0:
            pago_transporte = (x_ColombiaPayrollEngine.AUX_TRANSPORTE / 30) * days_worked
        
        self.logger.debug(f"Básico: {pago_basico}, Transporte: {pago_transporte}")
        return {'pago_basico': pago_basico, 'pago_transporte': pago_transporte}

    def _x_calculate_overtime(self, wage: float, overtime_hours: dict) -> dict:
        valor_hora = wage / 240
        rates = {
            'HED': 1.25, 'HEN': 1.75, 'RNOC': 0.35,
            'HED_DF': 2.00, 'HEN_DF': 2.50, 'RNOC_DF': 0.75
        }
        
        overtime_results = {}
        total_extras = 0.0
        for code, rate in rates.items():
            hours = float(overtime_hours.get(code, 0))
            amount = hours * valor_hora * rate
            overtime_results[f'EXT_{code}'] = amount
            total_extras += amount
            
        return {'items': overtime_results, 'total_extras': total_extras}

    def _x_calculate_ibc(self, pago_basico: float, total_extras: float) -> float:
        ibc = pago_basico + total_extras
        if ibc < x_ColombiaPayrollEngine.SMMLV:
            ibc = float(x_ColombiaPayrollEngine.SMMLV)
        return ibc

    def _x_calculate_statutory_deductions(self, ibc: float, wage: float, nivel_riesgo: int) -> dict:
        porc_salud = self.x_calcular_porcentaje_salud(wage)
        porc_arl = self.x_get_arl_rate(nivel_riesgo)
        
        fsp = 0.0
        if ibc > (x_ColombiaPayrollEngine.SMMLV * 4):
            fsp = ibc * 0.01

        return {
            'salud': ibc * porc_salud,
            'pension': ibc * 0.04,
            'fsp': fsp,
            'arl': ibc * porc_arl
        }

    def _x_extract_additional_inputs(self, overtime_hours: dict) -> dict:
        codes = [
            'EXT_PRIMA', 'EXT_CESANTIAS', 'EXT_INT_CES', 'EXT_VAC_COM',
            'EXT_VAC_COMP_DIN', 'EXT_DOTACION', 'EXT_BONIF_SAL', 'EXT_BONIF_NSAL',
            'EXT_COMISION', 'EXT_AUX_SAL', 'EXT_AUX_NSAL', 'EXT_OTRO_SAL',
            'EXT_OTRO_NSAL', 'EXT_INCAP_EC', 'EXT_INCAP_AT', 'EXT_LIC_REM',
            'EXT_APOYO_SOST', 'EXT_REINTEGRO'
        ]
        day_codes = [
            'EXT_VAC_COM_DIAS', 'EXT_VAC_COMP_DIN_DIAS', 'EXT_INCAP_EC_DIAS',
            'EXT_INCAP_AT_DIAS', 'EXT_LIC_REM_DIAS', 'EXT_LIC_NOREM_DIAS'
        ]
        
        results = {}
        for c in codes:
            results[c] = float(overtime_hours.get(c, 0))
        for dc in day_codes:
            results[dc] = int(overtime_hours.get(dc, 0))
            
        return results

    def _x_calculate_total_net(self, basic, overtime, statutory, additional, val_reten_fuente) -> float:
        total_devengado = (
            basic['pago_basico'] + basic['pago_transporte'] + 
            overtime['total_extras'] + 
            sum(v for k, v in additional.items() if not k.endswith('_DIAS'))
        )
        total_deducciones = (
            sum(statutory.values()) + val_reten_fuente
        )
        neto = total_devengado - total_deducciones
        self.logger.info(f"Neto a pagar calculado: {neto}")
        return neto

