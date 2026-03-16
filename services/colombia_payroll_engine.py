class ColombiaPayrollEngine:
    SMMLV = 1750905
    AUX_TRANSPORTE = 249095
    UVT = 53000

    @staticmethod
    def calculate_payroll(contract, worked_days_data, overtime_hours):
        wage = contract.get('wage', 0)
        # Forzar siempre a 30 días para todos los cálculos según requerimiento
        days_worked = 30
        total_days = 30

        pago_basico = (wage / 30) * days_worked

        pago_transporte = 0
        if wage <= (ColombiaPayrollEngine.SMMLV * 2) and days_worked > 0:
            pago_transporte = (
                ColombiaPayrollEngine.AUX_TRANSPORTE / 30) * days_worked

        valor_hora = wage / 240

        pago_hed = float(overtime_hours.get('HED', 0)) * valor_hora * 1.25
        pago_hen = float(overtime_hours.get('HEN', 0)) * valor_hora * 1.75
        pago_rnoc = float(overtime_hours.get('RNOC', 0)) * valor_hora * 0.35

        total_extras = pago_hed + pago_hen + pago_rnoc

        ibc = pago_basico + total_extras

        if total_days >= 30 and ibc < ColombiaPayrollEngine.SMMLV:
            ibc = ColombiaPayrollEngine.SMMLV

        ratio = wage / ColombiaPayrollEngine.SMMLV
        if ratio <= 1.0:
            porc_salud = 0.04
        elif ratio <= 3.0:
            porc_salud = 0.10
        else:
            porc_salud = 0.12

        deducción_salud = round(ibc * porc_salud, 2)
        deducción_pension = round(ibc * 0.04, 2)

        deduccion_fsp = 0
        if ibc > (ColombiaPayrollEngine.SMMLV * 4):
            deduccion_fsp = round(ibc * 0.01, 2)

        return {
            'EXT_BASICO': round(pago_basico, 2),
            'EXT_TRANS': round(pago_transporte, 2),
            'EXT_HED': round(pago_hed, 2),
            'EXT_HEN': round(pago_hen, 2),
            'EXT_RNOC': round(pago_rnoc, 2),
            'EXT_SALUD': deducción_salud,
            'EXT_PENSION': deducción_pension,
            'EXT_FSP': deduccion_fsp,
        }
