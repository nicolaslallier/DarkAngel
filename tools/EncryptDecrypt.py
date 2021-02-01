import win32security
import keyring
from cryptography.fernet import Fernet
class subsystem :

    def __init__(self):
        pass

    def getkey(self) :
        desc = win32security.GetFileSecurity(
            ".", win32security.OWNER_SECURITY_INFORMATION
        )
        sid = desc.GetSecurityDescriptorOwner()

        # https://www.programcreek.com/python/example/71691/win32security.ConvertSidToStringSid
        sidstr = win32security.ConvertSidToStringSid(sid)
        key=keyring.get_password("DarkAngel", sidstr)
        key=key.encode()
        self.cipher_suite = Fernet(key)

    def encrypt(self,chain) :
        return self.cipher_suite.encrypt(chain.encode()).decode()  # required to be bytes

    def decrypt(self,chain) :
        return self.cipher_suite.decrypt(chain.encode()).decode()
