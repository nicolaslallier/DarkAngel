import psycopg2
import tools.EncryptDecrypt
import secrets
import string

class init_db :
    """
            A class used to Initialise the database content

            ...

            Attributes
            ----------
            says_str : str
                a formatted string to print out what the animal says
            name : str
                the name of the animal
            sound : str
                the sound that the animal makes
            num_legs : int
                the number of legs the animal has (default 4)

            Methods
            -------
            gatherinfo()
                Will ask the user to enter the information required to connect to the databases
            printinfo()
                Print the information to connect to the database
            createinfra()
                Will create the infrastructure in the postgresql databases
                    - validate/create the user DarkAngel
                    - validate/create the DB DarkAngel
            """
    def __init__(self):
        self.dbhost = ""
        self.dbport = ""
        self.dbuser = ""
        self.dbpassword = ""
        self.passwordDBUser = ""
        self.encryptdecrypt = tools.EncryptDecrypt.subsystem()

    def gatherinfo(self):
        self.dbhost = input("Database host [127.0.0.1] : ") or "127.0.0.1"
        self.dbport = input("Database port : [5433]") or "5433"
        self.dbuser = input("Database user : [postgres]") or "postgres"
        self.dbpassword = input("Database password : ")

    def printinfo(self):
        print('Database host selected is : {0}'.format(self.dbhost))
        print('Database port selected is : {0}'.format(self.dbport))
        print('Database user/password selected is : {0} {1}'.format(self.dbuser, self.dbpassword))

    def createinfra(self):
        print("-"*80)
        print("Validating and creating the databases infrastructure")
        print("-" * 80)
        print("#Connecting to the database")
        conn = None
        try:
            conn = psycopg2.connect(
                host=self.dbhost,
                port=self.dbport,
                user=self.dbuser,
                password=self.dbpassword)
            print("    Connected to the database")
            print("#changing to have the autommit to on")
            # get the isolation leve for autocommit
            autocommit = psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT

            # set the isolation level for the connection's cursors
            # will raise ActiveSqlTransaction exception otherwise
            conn.set_isolation_level(autocommit)
            print("    Autocommit enabled")

            print("")
            # execute a statement
            print('#Checking if the user DaskAngel exist:')
            # create a cursor
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM pg_roles WHERE rolname='DarkAngel'")

            # display the PostgreSQL database server version
            userresult = cur.fetchone()
            cur.close()
            if userresult[0] == 1 :
                print("    User DarkAngel already exist, skipping creation")
            else :
                # execute a statement
                print('    The user does not exist, it will be created:')
                # create a cursor
                cur = conn.cursor()

                #Creating a unique password
                alphabet = string.ascii_letters + string.digits
                self.passwordDBUser = ''.join(secrets.choice(alphabet) for i in range(20))  # for a 20-character password

                cur.execute("CREATE ROLE \"DarkAngel\" WITH "
                            "LOGIN "
                            "NOSUPERUSER "
                            "NOCREATEDB "
                            "NOCREATEROLE "
                            "INHERIT "
                            "NOREPLICATION "
                            "CONNECTION LIMIT -1 "
                            "PASSWORD '"+ self.passwordDBUser + "';")
                print('    The user DarkAngel have been created:')
                cur.close()
            print('#Checking if the Database DaskAngel exist:')
            cur = conn.cursor()
            cur.execute("SELECT count(*) from pg_database WHERE datname = 'DarkAngel'")

            # display the PostgreSQL database server version
            dbresult = cur.fetchone()
            cur.close()

            if dbresult[0] == 1 :
                print("    DB DarkAngel already exist, skipping creation")
            else :
                # execute a statement
                print('    The DB does not exist, it will be created:')
                # create a cursor
                cur = conn.cursor()
                cur.execute("CREATE DATABASE \"DarkAngel\" "
                                "WITH "
                                "OWNER = \"DarkAngel\" "
                                "ENCODING = 'UTF8' "
                                "CONNECTION LIMIT = -1;")
                print('    The DB DarkAngel have been created:')
                cur.close()
        except (Exception, psycopg2.DatabaseError) as error:
            print(error)
        finally:
            if conn is not None:
                conn.close()
                print('Database connection closed.')

        print("Wrinting information to database.ini")


        self.encryptdecrypt.getkey()
        self.passwordDBUserEncrypted=self.encryptdecrypt.encrypt(self.passwordDBUser)
        f = open("database.ini", "w")
        f.write("[postgresql]\n")
        f.write("host = " + self.dbhost + "\n")
        f.write("port = " + self.dbport + "\n")
        f.write("database = DarkAngel\n")
        f.write("user = DarkAngel\n")
        f.write("password = " + self.passwordDBUserEncrypted + "\n")
        f.close()