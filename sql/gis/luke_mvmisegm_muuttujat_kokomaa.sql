CREATE TABLE IF NOT EXISTS public.luke_mvmisegmentit_muuttujat_kokomaa
(
    kuvio bigint PRIMARY KEY,
    "Region" int,
    "Maingroup" int,
    "Soiltype" int,
    "Drainage" int,
    "Fertility" int,
    "Species" int,
    "Structure" int,
    "Regime" int,
    "Age" int,
    "Carbon" numeric
);

ALTER TABLE public.luke_mvmisegmentit_muuttujat_kokomaa OWNER TO hiilikartta_dev;

GRANT ALL ON TABLE public.luke_mvmisegmentit_muuttujat_kokomaa TO hiilikartta_backend_user;
GRANT ALL ON TABLE public.luke_mvmisegmentit_muuttujat_kokomaa TO hiilikartta_dev;
