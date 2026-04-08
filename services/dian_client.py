import io
import zipfile
import base64
import requests
from lxml import etree
from logger_config import x_setup_logging
from services.soap_signer import x_SoapSigner

class x_DianClient:
    def __init__(self):
        self.logger = x_setup_logging('dian_client')
        self.soap_signer = x_SoapSigner()
        self.endpoints = {
            'hab': 'https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc',
            'prod': 'https://vpfe.dian.gov.co/WcfDianCustomerServices.svc'
        }

    def _x_create_zip_base64(self, filename, xml_content):
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
            zip_file.writestr(filename, xml_content.encode('utf-8'))
        return base64.b64encode(zip_buffer.getvalue()).decode('utf-8')

    def x_send_to_dian(self, xml_content, filename, dian_config, cert_config, output_dir='output_xmls'):
        mode = dian_config.get('operation_mode', 'test')  # default test
        url = self.endpoints['prod'] if mode == 'prod' else self.endpoints['hab']
        
        # TestSetId is dian_testing_id in dian_config -> mapped to testing_id
        test_set_id = dian_config.get('testing_id')
        
        action = 'http://wcf.dian.colombia/IWcfDianCustomerServices/SendTestSetAsync' if mode != 'prod' else 'http://wcf.dian.colombia/IWcfDianCustomerServices/SendNominaSync'
        
        zip_b64 = self._x_create_zip_base64(filename, xml_content)
        
        if mode == 'prod':
            soap_body = f"<wcf:SendNominaSync><wcf:fileName>{filename}</wcf:fileName><wcf:contentFile>{zip_b64}</wcf:contentFile></wcf:SendNominaSync>"
        else:
            if not test_set_id:
                raise ValueError("Se requiere 'TestSetId' (ID de Prueba DIAN) para enviar en modo Habilitación.")
            soap_body = f"<wcf:SendTestSetAsync><wcf:fileName>{filename}</wcf:fileName><wcf:contentFile>{zip_b64}</wcf:contentFile><wcf:testSetId>{test_set_id}</wcf:testSetId></wcf:SendTestSetAsync>"

        soap_template = f'<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:wcf="http://wcf.dian.colombia"><soap:Header xmlns:wsa="http://www.w3.org/2005/08/addressing"><wsa:Action>{action}</wsa:Action><wsa:To xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd" wsu:Id="id-wsa-to">{url}</wsa:To></soap:Header><soap:Body>{soap_body}</soap:Body></soap:Envelope>'

        self.logger.info(f"Firmando sobre SOAP para envío a DIAN (Modo: {mode})...")
        signed_soap = self.soap_signer.x_sign_soap_envelope(soap_template, cert_config)

        headers = {
            'Content-Type': 'application/soap+xml;charset=utf-8;action="{}"'.format(action)
        }

        self.logger.info(f"Enviando petición a la DIAN: {url}")
        response = requests.post(url, data=signed_soap.encode('utf-8'), headers=headers)
        
        return self._x_parse_dian_response(response)

    def _x_parse_dian_response(self, response):
        self.logger.info(f"Respuesta DIAN Status: {response.status_code}")
        
        result = {
            'status_code': response.status_code,
            'is_success': False,
            'dian_response_xml': response.text,
            'message': 'Error desconocido'
        }

        try:
            root = etree.fromstring(response.content)
            # Remove namespaces for easier querying
            for elem in root.getiterator():
                if not hasattr(elem.tag, 'find'): continue
                i = elem.tag.find('}')
                if i >= 0:
                    elem.tag = elem.tag[i+1:]
            
            # Extract AppResponse
            app_responses = root.xpath('//SendTestSetAsyncResult') or root.xpath('//SendNominaSyncResult')
            if app_responses:
                result['message'] = 'Recibido correctamente por el servicio.'
                zip_key = app_responses[0].findtext('ZipKey')
                if zip_key:
                    result['zip_key'] = zip_key
                    result['message'] += f" ZipKey: {zip_key}"
                
                error_msg = app_responses[0].findtext('ErrorMessage')
                if error_msg:
                    result['message'] += f" ErrorMessage: {error_msg}"
                    
                is_valid = app_responses[0].findtext('IsValid')
                if is_valid and is_valid.lower() == 'true':
                    result['is_success'] = True

            # Extract fault if it exists
            fault = root.xpath('//Fault')
            if fault:
                reason = fault[0].findtext('.//Text') or fault[0].findtext('faultstring')
                result['message'] = f"Falla SOAP de la DIAN: {reason}"
                
        except Exception as e:
            self.logger.error(f"Error parseando la respuesta DIAN: {e}")
            result['message'] = f"Error interpretando la respuesta de la DIAN. Code: {response.status_code}"

        return result

    def x_get_status_zip(self, track_id, dian_config, cert_config):
        mode = dian_config.get('operation_mode', 'test')
        url = self.endpoints['prod'] if mode == 'prod' else self.endpoints['hab']
        action = 'http://wcf.dian.colombia/IWcfDianCustomerServices/GetStatusZip'
        
        soap_body = f"<wcf:GetStatusZip><wcf:trackId>{track_id}</wcf:trackId></wcf:GetStatusZip>"
        soap_template = f'<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:wcf="http://wcf.dian.colombia"><soap:Header xmlns:wsa="http://www.w3.org/2005/08/addressing"><wsa:Action>{action}</wsa:Action><wsa:To xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd" wsu:Id="id-wsa-to">{url}</wsa:To></soap:Header><soap:Body>{soap_body}</soap:Body></soap:Envelope>'

        self.logger.info(f"Firmando sobre SOAP para consultar estado (TrackId: {track_id})...")
        signed_soap = self.soap_signer.x_sign_soap_envelope(soap_template, cert_config)

        headers = {
            'Content-Type': 'application/soap+xml;charset=utf-8;action="{}"'.format(action)
        }

        self.logger.info(f"Consultando estado en DIAN: {url}")
        response = requests.post(url, data=signed_soap.encode('utf-8'), headers=headers)
        
        return self._x_parse_dian_status_response(response)

    def _x_parse_dian_status_response(self, response):
        self.logger.info(f"Status Respuesta DIAN: {response.status_code}")
        
        result = {
            'status_code': response.status_code,
            'is_success': False,
            'dian_response_xml': response.text,
            'message': 'Error desconocido'
        }

        try:
            root = etree.fromstring(response.content)
            for elem in root.getiterator():
                if not hasattr(elem.tag, 'find'): continue
                i = elem.tag.find('}')
                if i >= 0:
                    elem.tag = elem.tag[i+1:]
            
            app_responses = root.xpath('//GetStatusZipResult')
            if app_responses:
                result['message'] = 'Respuesta procesada correctamente.'
                status_code = app_responses[0].findtext('.//StatusCode')
                status_desc = app_responses[0].findtext('.//StatusDescription')
                
                if status_desc:
                    result['message'] = f"Estado: {status_desc} (Code: {status_code})"
                
                is_valid = app_responses[0].findtext('.//IsValid')
                if is_valid and is_valid.lower() == 'true':
                    result['is_success'] = True
                    result['message'] = f"¡DOCUMENTO ACEPTADO! {status_desc}"

            fault = root.xpath('//Fault')
            if fault:
                reason = fault[0].findtext('.//Text') or fault[0].findtext('faultstring')
                result['message'] = f"Falla SOAP de la DIAN: {reason}"
                
        except Exception as e:
            self.logger.error(f"Error parseando el status de DIAN: {e}")
            result['message'] = f"Error leyendo status de la DIAN. Code: {response.status_code}"

        return result
