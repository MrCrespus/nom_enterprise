import os
from jinja2 import Environment, FileSystemLoader
from logger_config import x_setup_logging

class x_XMLGenerator:
    def __init__(self, template_dir=None):
        if not template_dir:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            template_dir = os.path.join(base_dir, 'templates')
        
        self.env = Environment(loader=FileSystemLoader(template_dir))
        self.logger = x_setup_logging('xml_generator')

    def x_render(self, data, template_name='nomina_dian.xml'):
        self.logger.info(f"Renderizando XML con plantilla: {template_name}")
        try:
            template = self.env.get_template(template_name)
            return template.render(data)
        except Exception:
            self.logger.error(f"Error al obtener la plantilla {template_name}")
            raise

    def x_save_to_file(self, xml_content, filename, output_dir='output_xmls'):
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            self.logger.info(f"Creado directorio de salida: {output_dir}")
        
        file_path = os.path.join(output_dir, filename)
        with open(file_path, "w", encoding='utf-8') as f:
            f.write(xml_content)
        
        self.logger.info(f"Archivo XML guardado satisfactoriamente en: {file_path}")
        return file_path
