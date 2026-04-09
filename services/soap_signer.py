import base64
import uuid
import datetime
from lxml import etree
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.hazmat.primitives import hashes
from cryptography import x509
from services.signature_service import x_SignatureService
import re
from cryptography.hazmat.primitives.serialization import pkcs12, load_pem_private_key

NS_MAP = {
    'soap': "http://www.w3.org/2003/05/soap-envelope",
    'wsa': "http://www.w3.org/2005/08/addressing",
    'wcf': "http://wcf.dian.colombia",
    'wsse': "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd",
    'wsu': "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd",
    'dsig': "http://www.w3.org/2000/09/xmldsig#"
}

class x_SoapSigner:
    def __init__(self):
        self.signature_service = x_SignatureService()

    def x_get_private_key_and_cert(self, cert_config):
        cert_content = cert_config.get('content')
        cert_password = cert_config.get('password')
        private_key_pem = cert_config.get('private_key_pem')
        public_key_pem = cert_config.get('public_key_pem')

        logger = self.signature_service.logger
        cert_chain = []
        private_key = None

        def _decode_odoo_binary(value):
            """Los campos Binary de Odoo se devuelven como string base64.
            Intenta decodificar y detectar si el resultado es PEM o DER/PKCS12."""
            if not value:
                return None, 'empty'
            try:
                raw = base64.b64decode(value)
                if raw.strip().startswith(b'-----'):
                    return raw, 'pem'
                return raw, 'der_or_pkcs12'
            except Exception:
                # Si falla el decode, puede que ya sea bytes PEM directos
                if isinstance(value, str) and value.strip().startswith('-----'):
                    return value.encode(), 'pem_raw'
                return None, 'unknown'

        # --- Intento 1: Cargar desde claves PEM separadas (certificate.key) ---
        if private_key_pem:
            logger.info("SOAP Cert: intentando cargar desde private_key_pem (certificate.key)...")
            try:
                priv_raw, priv_type = _decode_odoo_binary(private_key_pem)
                logger.info(f"SOAP Cert: private_key tipo detectado: {priv_type}, tamaño: {len(priv_raw) if priv_raw else 0}")
                if priv_raw and priv_type in ('pem', 'pem_raw'):
                    try:
                        private_key = load_pem_private_key(priv_raw, password=str(cert_password).encode() if cert_password else None)
                    except Exception:
                        private_key = load_pem_private_key(priv_raw, password=None)
                    logger.info("SOAP Cert: clave privada PEM cargada exitosamente.")
                else:
                    logger.warning(f"SOAP Cert: private_key_pem no es PEM válido (tipo: {priv_type}). Se intentará con content.")
            except Exception as e:
                logger.warning(f"SOAP Cert: falló carga de private_key_pem: {e}")
                private_key = None

            if private_key and public_key_pem:
                try:
                    pub_raw, pub_type = _decode_odoo_binary(public_key_pem)
                    logger.info(f"SOAP Cert: public_key tipo detectado: {pub_type}, tamaño: {len(pub_raw) if pub_raw else 0}")
                    if pub_raw:
                        try:
                            cert_chain = [x509.load_pem_x509_certificate(pub_raw)]
                            logger.info("SOAP Cert: certificado público PEM cargado exitosamente.")
                        except ValueError:
                            pem_str = pub_raw.decode('utf-8', errors='ignore')
                            certs_found = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', pem_str, re.DOTALL)
                            for c_str in certs_found:
                                cert_chain.append(x509.load_pem_x509_certificate(c_str.encode('utf-8')))
                            logger.info(f"SOAP Cert: {len(cert_chain)} certificados encontrados en cadena PEM.")
                except Exception as e:
                    logger.warning(f"SOAP Cert: falló carga de public_key_pem: {e}")

        # --- Intento 2: Cargar certificado desde content (PKCS12 o PEM completo) ---
        # Si aún no tenemos certificado, buscamos en content (independientemente de si ya tenemos la clave)
        if (not cert_chain or not private_key) and cert_content:
            logger.info("SOAP Cert: intentando cargar (extra) desde content (PKCS12/PEM)...")
            try:
                raw, content_type = _decode_odoo_binary(cert_content)
                logger.info(f"SOAP Cert: content tipo detectado: {content_type}, tamaño: {len(raw) if raw else 0}")

                if raw and content_type in ('pem', 'pem_raw'):
                    # Si no teníamos clave, la buscamos aquí
                    if not private_key:
                        try:
                            private_key = load_pem_private_key(raw, password=str(cert_password).encode() if cert_password else None)
                        except Exception:
                            private_key = load_pem_private_key(raw, password=None)
                    
                    # Buscamos certificados en el content PEM
                    pem_str = raw.decode('utf-8', errors='ignore')
                    certs_found = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', pem_str, re.DOTALL)
                    for c_str in certs_found:
                        cert_chain.append(x509.load_pem_x509_certificate(c_str.encode('utf-8')))
                    
                    if certs_found:
                        logger.info(f"SOAP Cert: {len(certs_found)} certificados encontrados en content PEM.")

                elif raw and content_type == 'der_or_pkcs12':
                    password_bytes = str(cert_password).encode() if cert_password and str(cert_password).lower() not in ('false', 'none', '') else None
                    try:
                        pkcs12_key, main_cert, additional_certs = pkcs12.load_key_and_certificates(raw, password_bytes)
                        if not private_key:
                            private_key = pkcs12_key
                        if not cert_chain:
                            cert_chain = [main_cert] + (additional_certs if additional_certs else [])
                        logger.info(f"SOAP Cert: PKCS12 cargado exitosamente. Certs en cadena: {len(cert_chain)}")
                    except Exception as e:
                        if not private_key: # Solo es un error fatal si no tenemos ninguna clave aún
                            logger.error(f"SOAP Cert: falló deserialización PKCS12: {e}")
                            raise Exception(f"No se pudo deserializar el certificado PKCS12: {e}")
                else:
                    logger.warning(f"SOAP Cert: content en formato desconocido o vacío para extracción de certificado (tipo: {content_type})")
            except Exception as e:
                if not private_key or not cert_chain:
                    logger.error(f"SOAP Cert: error cargando desde content: {e}")
                    raise

        if not private_key:
            logger.error("SOAP Cert: no se pudo cargar la clave privada desde ninguna fuente.")
        if not cert_chain:
            logger.error("SOAP Cert: no se encontraron certificados X509 en ninguna fuente.")

        return private_key, cert_chain[0] if cert_chain else None


    def x_sign_soap_envelope(self, envelope_xml, cert_config):
        private_key, main_cert = self.x_get_private_key_and_cert(cert_config)
        if not private_key or not main_cert:
            raise Exception("No valid certificate/private key found for SOAP WS-Security signing.")

        root = etree.fromstring(envelope_xml.encode('utf-8'))
        
        # Ensure Header exists
        header = root.find('.//soap:Header', namespaces=NS_MAP)
        if header is None:
            header = etree.Element("{http://www.w3.org/2003/05/soap-envelope}Header")
            root.insert(0, header)
            
        # Odoo ONLY signs wsa:To, not Body, not Action, not MessageID!
        to_node_id = "id-wsa-to"
        
        # Build Security Header
        security = etree.Element("{http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd}Security", nsmap={'wsse': 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd', 'wsu': 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd'})
        header.insert(0, security)

        # Timestamp
        timestamp = etree.SubElement(security, "{http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd}Timestamp")
        now = datetime.datetime.utcnow()
        created = etree.SubElement(timestamp, "{http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd}Created")
        created.text = now.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        expires = etree.SubElement(timestamp, "{http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd}Expires")
        expires.text = (now + datetime.timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        
        # BinarySecurityToken
        uuid_bst = "SecurityToken-" + str(uuid.uuid4())
        bst = etree.SubElement(security, "{http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd}BinarySecurityToken")
        bst.set("{http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd}Id", uuid_bst)
        bst.set("EncodingType", "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary")
        bst.set("ValueType", "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-x509-token-profile-1.0#X509v3")
        bst.text = base64.b64encode(main_cert.public_bytes(Encoding.DER)).decode()

        # Signature
        signature = etree.SubElement(security, "{http://www.w3.org/2000/09/xmldsig#}Signature", nsmap={'ds': "http://www.w3.org/2000/09/xmldsig#"})
        
        # Build ds:SignedInfo
        signed_info = etree.SubElement(signature, "{http://www.w3.org/2000/09/xmldsig#}SignedInfo")
        c14n = etree.SubElement(signed_info, "{http://www.w3.org/2000/09/xmldsig#}CanonicalizationMethod", Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#")
        etree.SubElement(c14n, "{http://www.w3.org/2001/10/xml-exc-c14n#}InclusiveNamespaces", PrefixList="wsa soap wcf")
        etree.SubElement(signed_info, "{http://www.w3.org/2000/09/xmldsig#}SignatureMethod", Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256")
        
        # Reference list (ONLY wsa:To!)
        refs = [to_node_id]
        
        for ref_id in refs:
            ref = etree.SubElement(signed_info, "{http://www.w3.org/2000/09/xmldsig#}Reference", URI=f"#{ref_id}")
            transforms = etree.SubElement(ref, "{http://www.w3.org/2000/09/xmldsig#}Transforms")
            trans = etree.SubElement(transforms, "{http://www.w3.org/2000/09/xmldsig#}Transform", Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#")
            etree.SubElement(trans, "{http://www.w3.org/2001/10/xml-exc-c14n#}InclusiveNamespaces", PrefixList="soap wcf")
            etree.SubElement(ref, "{http://www.w3.org/2000/09/xmldsig#}DigestMethod", Algorithm="http://www.w3.org/2001/04/xmlenc#sha256")
            etree.SubElement(ref, "{http://www.w3.org/2000/09/xmldsig#}DigestValue")

        etree.SubElement(signature, "{http://www.w3.org/2000/09/xmldsig#}SignatureValue")
        
        key_info = etree.SubElement(signature, "{http://www.w3.org/2000/09/xmldsig#}KeyInfo")
        sec_token_ref = etree.SubElement(key_info, "{http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd}SecurityTokenReference")
        ref_tok = etree.SubElement(sec_token_ref, "{http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd}Reference")
        ref_tok.set("URI", f"#{uuid_bst}")
        ref_tok.set("ValueType", "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-x509-token-profile-1.0#X509v3")

        self.signature_service._x_remove_tail_and_text_in_hierarchy(root)
        self.signature_service._x_reference_digests(signed_info)
        self.signature_service._x_fill_signature(signature, private_key)

        return etree.tostring(root, encoding='utf-8', xml_declaration=True, pretty_print=False).decode()
