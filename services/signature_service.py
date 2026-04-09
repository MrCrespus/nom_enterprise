import base64
from lxml import etree
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding, pkcs12, load_pem_private_key
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
import hashlib
from copy import deepcopy
from logger_config import x_setup_logging
import datetime
import re

NS_MAP = {'ds': "http://www.w3.org/2000/09/xmldsig#"}


class x_SignatureService:
    @staticmethod
    def _x_canonicalize_node(node, **kwargs):
        return etree.tostring(node, method="c14n", with_comments=False, **kwargs)

    @classmethod
    def _x_get_uri(cls, uri, reference, base_uri=""):
        transform_nodes = reference.findall(".//{*}Transform")
        exc_c14n = bool(transform_nodes) and transform_nodes[0].attrib.get(
            'Algorithm') == 'http://www.w3.org/2001/10/xml-exc-c14n#'
        prefix_list = []
        if exc_c14n:
            inclusive_ns_node = transform_nodes[0].find(
                ".//{*}InclusiveNamespaces")
            if inclusive_ns_node is not None and inclusive_ns_node.attrib.get('PrefixList'):
                prefix_list = inclusive_ns_node.attrib.get('PrefixList').split(' ')

        node = deepcopy(reference.getroottree().getroot())
        if uri == base_uri:
            for signature in node.findall('.//ds:Signature', namespaces=NS_MAP):
                if signature.tail:
                    if (previous := signature.getprevious()) is not None:
                        previous.tail = "".join(
                            [previous.tail or "", signature.tail or ""])
                    else:
                        signature.getparent().text = "".join(
                            [signature.getparent().text or "", signature.tail or ""])
                signature.getparent().remove(signature)
            return cls._x_canonicalize_node(node, exclusive=exc_c14n, inclusive_ns_prefixes=prefix_list)

        if uri.startswith("#"):
            path = "//*[@*[local-name() = '{}' ]=$uri]"
            results = node.xpath(path.format("Id"), uri=uri.lstrip("#"))
            if len(results) == 1:
                return cls._x_canonicalize_node(results[0], exclusive=exc_c14n, inclusive_ns_prefixes=prefix_list)
            if len(results) > 1:
                raise Exception(f"Ambiguous reference URI {uri} resolved to {len(results)} nodes")

        raise Exception(f'URI {uri} not found')

    @classmethod
    def _x_reference_digests(cls, node, base_uri=""):
        for reference in node.findall("ds:Reference", namespaces=NS_MAP):
            ref_node = cls._x_get_uri(reference.get("URI", ""),
                                 reference, base_uri=base_uri)
            lib = hashlib.new("sha256", ref_node)
            reference.find("ds:DigestValue",
                           namespaces=NS_MAP).text = base64.b64encode(lib.digest()).decode()

    @classmethod
    def _x_fill_signature(cls, node, private_key):
        signed_info_xml = node.find("ds:SignedInfo", namespaces=NS_MAP)
        exc_c14n = signed_info_xml.find(".//{*}CanonicalizationMethod").attrib.get(
            'Algorithm') == 'http://www.w3.org/2001/10/xml-exc-c14n#'
        prefix_list = []
        if exc_c14n:
            inclusive_ns_node = signed_info_xml.find(
                ".//{*}CanonicalizationMethod").find(".//{*}InclusiveNamespaces")
            if inclusive_ns_node is not None and inclusive_ns_node.attrib.get('PrefixList'):
                prefix_list = inclusive_ns_node.attrib.get('PrefixList').split(' ')

        canonical_si = cls._x_canonicalize_node(
            signed_info_xml, exclusive=exc_c14n, inclusive_ns_prefixes=prefix_list)

        signature = private_key.sign(
            canonical_si,
            asym_padding.PKCS1v15(),
            hashes.SHA256()
        )
        node.find("ds:SignatureValue", namespaces=NS_MAP).text = base64.b64encode(
            signature).decode()

    @staticmethod
    def _x_remove_tail_and_text_in_hierarchy(node):
        node.tail = None
        if list(node):
            node.text = None
            for child in node:
                x_SignatureService._x_remove_tail_and_text_in_hierarchy(child)

    def __init__(self):
        self.logger = x_setup_logging('signature_service')

    def x_sign(self, xml_content, cert_content_b64, cert_password):
        try:
            cert_data_bytes = base64.b64decode(cert_content_b64)
            if cert_data_bytes.strip().startswith(b'-----'):
                return self.x_sign_with_pem(
                    xml_content,
                    cert_content_b64,
                    cert_content_b64,
                    cert_password
                )

            private_key, main_cert, additional_certs = pkcs12.load_key_and_certificates(
                cert_data_bytes, cert_password.encode() if cert_password else None
            )
            cert_chain = [main_cert] + (additional_certs if additional_certs else [])
            return self._x_build_signature_structure(xml_content, private_key, cert_chain)

        except Exception as e:
            self.logger.error(f"Error al firmar con PKCS12: {e}")
            raise

    def x_sign_with_pem(self, xml_content, private_key_pem_b64, public_key_pem_b64=None, password=None):
        try:
            private_key_bytes = base64.b64decode(private_key_pem_b64)
            public_key_bytes = base64.b64decode(
                public_key_pem_b64) if public_key_pem_b64 else private_key_bytes

            if not private_key_bytes.strip().startswith(b'-----'):
                raise ValueError("El contenido no tiene cabeceras PEM válidas.")

            try:
                private_key = load_pem_private_key(
                    private_key_bytes,
                    password=password.encode() if password else None
                )
            except Exception:
                private_key = load_pem_private_key(private_key_bytes, password=None)

            cert_chain = []
            try:
                # Intentamos buscar certificados en los bytes de la clave pública
                pem_str_pub = public_key_bytes.decode('utf-8', errors='ignore')
                certs_found = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', pem_str_pub, re.DOTALL)
                
                # Si no hay en la pública, buscamos en la privada (por si es un PEM combo)
                if not certs_found:
                    pem_str_priv = private_key_bytes.decode('utf-8', errors='ignore')
                    certs_found = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', pem_str_priv, re.DOTALL)

                for c_str in certs_found:
                    cert_chain.append(x509.load_pem_x509_certificate(c_str.encode('utf-8')))

                if not cert_chain:
                    self.logger.warning("No se encontraron certificados en los datos PEM (ni en clave pública ni privada).")
                    raise Exception("No se encontraron certificados en los datos PEM.")

            except Exception as e:
                self.logger.error(f"Error extrayendo certificado X509 del PEM: {e}")
                raise Exception(f"No se pudo extraer el certificado X509 del PEM: {e}")

            return self._x_build_signature_structure(xml_content, private_key, cert_chain)

        except Exception as e:
            self.logger.error(f"Error al firmar con PEM: {e}")
            raise

    def _x_build_signature_structure(self, xml_content, private_key, cert_chain):
        if not cert_chain:
            raise ValueError("La cadena de certificados está vacía.")
        main_cert = cert_chain[0]
        root = etree.fromstring(xml_content.encode('utf-8'))

        # 1. Configurar extensiones UBL y obtener el nodo de firma
        ubl_extensions = self._x_setup_ubl_extensions(root)
        
        unique_id = f"Signature-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        key_info_id = f"{unique_id}-KeyInfo"
        signed_props_id = f"xmldsig-{unique_id}-signedprops"

        ubl_extension = etree.SubElement(ubl_extensions, "{urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2}UBLExtension")
        ext_content = etree.SubElement(ubl_extension, "{urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2}ExtensionContent")
        
        signature_node = etree.SubElement(ext_content, "{http://www.w3.org/2000/09/xmldsig#}Signature", Id=unique_id)

        # 2. Construir SignedInfo y Referencias
        signed_info = self._x_create_signed_info(signature_node, key_info_id, signed_props_id)
        
        # 3. Construir KeyInfo con X509Data
        self._x_add_key_info(signature_node, key_info_id, main_cert)

        # 4. Construir Object y QualifyingProperties
        self._x_add_qualifying_properties(signature_node, unique_id, signed_props_id, cert_chain)

        # 5. Finalizar: Canonización, Digests y Firma Final
        self._x_remove_tail_and_text_in_hierarchy(root)
        self._x_reference_digests(signed_info)
        self._x_fill_signature(signature_node, private_key)

        self.logger.info(f"Firma digital generada exitosamente.")
        return etree.tostring(root, encoding='utf-8', xml_declaration=True, pretty_print=False).decode()

    def _x_setup_ubl_extensions(self, root):
        ubl_extensions = root.find('.//{*}UBLExtensions')
        if ubl_extensions is None:
            ext_ns = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
            ubl_extensions = etree.Element("{%s}UBLExtensions" % ext_ns)
            root.insert(0, ubl_extensions)
        return ubl_extensions

    def _x_create_signed_info(self, signature_node, key_info_id, signed_props_id):
        signed_info = etree.SubElement(signature_node, "{http://www.w3.org/2000/09/xmldsig#}SignedInfo")
        etree.SubElement(signed_info, "{http://www.w3.org/2000/09/xmldsig#}CanonicalizationMethod",
                         Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315")
        etree.SubElement(signed_info, "{http://www.w3.org/2000/09/xmldsig#}SignatureMethod",
                         Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256")

        # Referencia al documento (URI="")
        ref_doc = etree.SubElement(signed_info, "{http://www.w3.org/2000/09/xmldsig#}Reference", URI="")
        transforms = etree.SubElement(ref_doc, "{http://www.w3.org/2000/09/xmldsig#}Transforms")
        etree.SubElement(transforms, "{http://www.w3.org/2000/09/xmldsig#}Transform",
                         Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature")
        etree.SubElement(ref_doc, "{http://www.w3.org/2000/09/xmldsig#}DigestMethod",
                         Algorithm="http://www.w3.org/2001/04/xmlenc#sha256")
        etree.SubElement(ref_doc, "{http://www.w3.org/2000/09/xmldsig#}DigestValue")

        # Referencia a KeyInfo
        ref_key = etree.SubElement(signed_info, "{http://www.w3.org/2000/09/xmldsig#}Reference", URI=f"#{key_info_id}")
        etree.SubElement(ref_key, "{http://www.w3.org/2000/09/xmldsig#}DigestMethod", Algorithm="http://www.w3.org/2001/04/xmlenc#sha256")
        etree.SubElement(ref_key, "{http://www.w3.org/2000/09/xmldsig#}DigestValue")

        # Referencia a SignedProperties
        ref_props = etree.SubElement(signed_info, "{http://www.w3.org/2000/09/xmldsig#}Reference", URI=f"#{signed_props_id}")
        etree.SubElement(ref_props, "{http://www.w3.org/2000/09/xmldsig#}DigestMethod", Algorithm="http://www.w3.org/2001/04/xmlenc#sha256")
        etree.SubElement(ref_props, "{http://www.w3.org/2000/09/xmldsig#}DigestValue")

        etree.SubElement(signature_node, "{http://www.w3.org/2000/09/xmldsig#}SignatureValue")
        return signed_info

    def _x_add_key_info(self, signature_node, key_info_id, main_cert):
        key_info = etree.SubElement(signature_node, "{http://www.w3.org/2000/09/xmldsig#}KeyInfo", Id=key_info_id)
        x509_data = etree.SubElement(key_info, "{http://www.w3.org/2000/09/xmldsig#}X509Data")
        x509_cert = etree.SubElement(x509_data, "{http://www.w3.org/2000/09/xmldsig#}X509Certificate")
        cert_b64 = base64.b64encode(main_cert.public_bytes(Encoding.DER)).decode()
        x509_cert.text = cert_b64

    def _x_add_qualifying_properties(self, signature_node, unique_id, signed_props_id, cert_chain):
        object_node = etree.SubElement(signature_node, "{http://www.w3.org/2000/09/xmldsig#}Object")
        qualifying_props = etree.SubElement(object_node, "{http://uri.etsi.org/01903/v1.3.2#}QualifyingProperties", Target=f"#{unique_id}")
        signed_props = etree.SubElement(qualifying_props, "{http://uri.etsi.org/01903/v1.3.2#}SignedProperties", Id=signed_props_id)
        signed_sig_props = etree.SubElement(signed_props, "{http://uri.etsi.org/01903/v1.3.2#}SignedSignatureProperties")

        # SigningTime
        signing_time = etree.SubElement(signed_sig_props, "{http://uri.etsi.org/01903/v1.3.2#}SigningTime")
        signing_time.text = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

        # SigningCertificate
        signing_cert = etree.SubElement(signed_sig_props, "{http://uri.etsi.org/01903/v1.3.2#}SigningCertificate")
        for cert_item in cert_chain:
            cert_node = etree.SubElement(signing_cert, "{http://uri.etsi.org/01903/v1.3.2#}Cert")
            cert_digest = etree.SubElement(cert_node, "{http://uri.etsi.org/01903/v1.3.2#}CertDigest")
            etree.SubElement(cert_digest, "{http://www.w3.org/2000/09/xmldsig#}DigestMethod", Algorithm="http://www.w3.org/2001/04/xmlenc#sha256")

            digest_val = base64.b64encode(cert_item.fingerprint(hashes.SHA256())).decode()
            etree.SubElement(cert_digest, "{http://www.w3.org/2000/09/xmldsig#}DigestValue").text = digest_val

            issuer_serial = etree.SubElement(cert_node, "{http://uri.etsi.org/01903/v1.3.2#}IssuerSerial")
            etree.SubElement(issuer_serial, "{http://www.w3.org/2000/09/xmldsig#}X509IssuerName").text = cert_item.issuer.rfc4514_string()
            etree.SubElement(issuer_serial, "{http://www.w3.org/2000/09/xmldsig#}X509SerialNumber").text = str(cert_item.serial_number)

        sig_policy_id_node = etree.SubElement(signed_sig_props, "{http://uri.etsi.org/01903/v1.3.2#}SignaturePolicyIdentifier")
        sig_policy_id = etree.SubElement(sig_policy_id_node, "{http://uri.etsi.org/01903/v1.3.2#}SignaturePolicyId")
        sig_policy_id_id = etree.SubElement(sig_policy_id, "{http://uri.etsi.org/01903/v1.3.2#}SigPolicyId")
        etree.SubElement(sig_policy_id_id, "{http://uri.etsi.org/01903/v1.3.2#}Identifier").text = "https://facturaelectronica.dian.gov.co/politicadefirma/v2/politicadefirmav2.pdf"
        etree.SubElement(sig_policy_id_id, "{http://uri.etsi.org/01903/v1.3.2#}Description").text = "Política de firma para facturas electrónicas de la República de Colombia"
        sig_policy_hash = etree.SubElement(sig_policy_id, "{http://uri.etsi.org/01903/v1.3.2#}SigPolicyHash")
        etree.SubElement(sig_policy_hash, "{http://www.w3.org/2000/09/xmldsig#}DigestMethod", Algorithm="http://www.w3.org/2001/04/xmlenc#sha256")
        etree.SubElement(sig_policy_hash, "{http://www.w3.org/2000/09/xmldsig#}DigestValue").text = "dMoMvtcG5aIzgYo0tIsSQeVJBDnUnfSOfBpxXrmor0Y="

        signer_role = etree.SubElement(signed_sig_props, "{http://uri.etsi.org/01903/v1.3.2#}SignerRole")
        claimed_roles = etree.SubElement(signer_role, "{http://uri.etsi.org/01903/v1.3.2#}ClaimedRoles")
        etree.SubElement(claimed_roles, "{http://uri.etsi.org/01903/v1.3.2#}ClaimedRole").text = "supplier"
