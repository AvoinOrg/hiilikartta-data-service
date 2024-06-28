
To be able to run the hiilikartta-data-service, you may have to do following steps (it does not mean you can then use it but at least you get it to run):

1. If you have not already done, for example, with the [climate-map app](https://github.com/AvoinOrg/climate-map), you may need to run once `docker network create climate-map-network`.
2. Copy the .env.template to .env
3. Make changes to .env file
    1. Change the value of `GIT_SSH_KEY` to `GIT_SSH_KEY=C:\Users\<your username>\.ssh` or to whatever path is correct fot you
    2. Make following changes (the databases do not have to exist to be able to run the backend)
        ```
        GIS_PG_PASSWORD="1234"
        GIS_PG_USER="dev"
        GIS_PG_DB="gis_dev_db"
        GIS_PG_PORT=5433
        GIS_PG_HOST="gis_db"

        STATE_PG_PASSWORD="1234"
        STATE_PG_USER="dev"
        STATE_PG_DB="dev_db"
        STATE_PG_PORT=5432
        STATE_PG_HOST="db"
        ```
4. Create data folder under the hiilikartta-data-service root folder
5. Create sample aluekertoimet.csv file in the data folder.
   I suggest to take look into
     - last page of the https://www.syke.fi/download/noname/%7B1D48DE00-C536-4794-9093-008FFC98FA5E%7D/182312
     - data_loader.py
     - calculator.py
     - maybe the jupyter notebooks
   
   I created the file with the following sample contents but I am not sure if it is correct:
     ```
     Lyhenne,zoning_code,"Kasvillisuuden hiiltä säästyy","Maaperän hiiltä säästyy"
     A,A,0.8,0.8
     ```
6. Create sample BiomassCurves.txt file in the data folder. I gave it the following, maybe incorrect sample contents:
    ```
    Region, Maingroup, Soiltype, Drainage, Fertility, Species, Structure, Regime
    19, 3, 1, 1, 9, 1, 1, 401
    ```
7. Run `docker-compose up --build` (maybe `docker-compose up` would have been enough)

When you have the hiilikartta-data-service running,
- the hiilikartta-data-service API can be found from the URL [http://localhost:8000](http://localhost:8000) and
- jupyter notebook UI can be found from the URL [http://localhost:8888](http://localhost:8888)
