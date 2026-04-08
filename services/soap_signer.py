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
        
        cert_chain = []
        private_key = None

        def load_from_pem_content(priv_b64, pub_b64):
            priv_bytes = base64.b64decode(priv_b64)
            pub_bytes = base64.b64decode(pub_b64) if pub_b64 else priv_bytes
            nonlocal private_key, cert_chain
            try:
                private_key = load_pem_private_key(priv_bytes, password=str(cert_password).encode() if cert_password else None)
            except Exception:
                private_key = load_pem_private_key(priv_bytes, password=None)

            try:
                cert = x509.load_pem_x509_certificate(pub_bytes)
                cert_chain = [cert]
            except ValueError:
                pem_str = pub_bytes.decode('utf-8', errors='ignore')
                certs_found = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', pem_str, re.DOTALL)
                for c_str in certs_found:
                    cert_chain.append(x509.load_pem_x509_certificate(c_str.encode('utf-8')))

        if private_key_pem:
            try:
                load_from_pem_content(private_key_pem, public_key_pem)
            except Exception:
                private_key = None

        if not private_key and cert_content:
            cert_data_bytes = base64.b64decode(cert_content)
            if cert_data_bytes.strip().startswith(b'-----'):
                load_from_pem_content(cert_content, cert_content)
            else:
                password_bytes = str(cert_password).encode() if cert_password and str(cert_password).lower() != 'false' else None
                try:
                    private_key, main_cert, additional_certs = pkcs12.load_key_and_certificates(
                        cert_data_bytes, password_bytes
                    )
                    cert_chain = [main_cert] + (additional_certs if additional_certs else [])
                except Exception as e:
                    raise Exception(f"Failed to deserialize PKCS12 data: {e}")
            
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
