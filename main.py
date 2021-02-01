# Include standard modules
import argparse
import tools.init_db
import tools.databases

def initdb() :

    initdb=tools.init_db.init_db()
    gatherinfo = True

    while (gatherinfo):
        initdb.gatherinfo()
        initdb.printinfo()
        if input("Does the information are good (Yes/No): ") == "Yes":
            gatherinfo = False

    initdb.createinfra()

def createsample() :
    db=tools.databases.databases()


if __name__ == "__main__":
    # Initiate the parser
    parser = argparse.ArgumentParser()
    parser.add_argument("-V", "--version", help="show program version", action="store_true")
    parser.add_argument("--init_db", help="Initialisation of the database", action="store_true")
    parser.add_argument("--create_sample", help="Creation of sample data", action="store_true")

    # Read arguments from the command line
    args = parser.parse_args()

    # Check for --version or -V
    if args.version:
        print("This is myprogram version 0.1")

    if args.init_db :
        initdb()

    if args.create_sample :
        createsample()

