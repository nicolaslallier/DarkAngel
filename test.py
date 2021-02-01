import keyring
print(keyring.get_password("test", "secret_username"))