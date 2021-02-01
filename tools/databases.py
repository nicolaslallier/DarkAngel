from configparser import ConfigParser

import psycopg2

import tools.EncryptDecrypt

class databases :
    """
                A class used to Initialise the database content

                ...

                Attributes
                ----------

                Methods
                -------
                config()
                IN
                    filename='database.ini'     file name of the config file
                    section='postgresql'        Section of the config file, in case there is many DC config
                OUT
                    paramters for connecting to the DB

                    read the content of the config file and parse the option
                """
    def config(self,filename='database.ini', section='postgresql'):
        encryptdecrypt = tools.EncryptDecrypt.subsystem()
        encryptdecrypt.getkey()
        # create a parser
        parser = ConfigParser()
        # read config file
        parser.read(filename)

        # get section, default to postgresql
        db = {}
        if parser.has_section(section):
            params = parser.items(section)
            for param in params:
                if param[0] != "password":
                    db[param[0]] = param[1]
                else:
                    db[param[0]] = encryptdecrypt.decrypt(param[1])
        else:
            raise Exception('Section {0} not found in the {1} file'.format(section, filename))

        return(db)
    def __init__(self):
        self.params = self.config()
        """ Connect to the PostgreSQL database server """
        conn = None
        try:
            # read connection parameters

            # connect to the PostgreSQL server
            print('Connecting to the PostgreSQL database...')
            self.conn = psycopg2.connect(**params)

            # create a cursor
            cur = conn.cursor()

            # execute a statement
            print('PostgreSQL database version:')
            cur.execute('SELECT version()')

            # display the PostgreSQL database server version
            db_version = cur.fetchone()
            print(db_version)

            # close the communication with the PostgreSQL
            cur.close()
        except (Exception, psycopg2.DatabaseError) as error:
            print(error)
        finally:
            if conn is not None:
                conn.close()
                print('Database connection closed.')