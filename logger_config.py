import logging
from datetime import datetime
import os

def x_setup_logging(name=__name__, company_name=None):
    # Generar prefijo de logger único si hay empresa para evitar colisiones de handlers
    logger_name = f"{name}_{company_name}" if company_name else name
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)

    if logger.hasHandlers():
        return logger

    # Definir directorio de logs
    if company_name:
        import re
        safe_company = re.sub(r'[^\w\s-]', '', company_name).strip().replace(' ', '_')
        log_dir = os.path.join('logs', safe_company)
    else:
        log_dir = 'logs'

    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Generar nombre de archivo con fecha
    current_date = datetime.now().strftime('%Y-%m-%d')
    log_filename = os.path.join(log_dir, f'payroll_api_{current_date}.log')

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_filename, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
