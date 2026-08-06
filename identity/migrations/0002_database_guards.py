from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE public.user_account
DROP CONSTRAINT user_account_workshop_id_c527df3f_fk_workshop_id,
ADD CONSTRAINT user_account_workshop_id_c527df3f_fk_workshop_id
FOREIGN KEY (workshop_id) REFERENCES public.workshop(id) ON DELETE RESTRICT;

ALTER TABLE public.user_account
DROP CONSTRAINT user_account_workshop_role_id_fb630c41_fk_workshop_role_id,
ADD CONSTRAINT user_account_workshop_role_id_fb630c41_fk_workshop_role_id
FOREIGN KEY (workshop_role_id) REFERENCES public.workshop_role(id) ON DELETE RESTRICT;

CREATE FUNCTION public.sb01_user_write_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
DECLARE
    role_workshop_id bigint;
    role_machine_key text;
    role_status text;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.account_role IS DISTINCT FROM OLD.account_role THEN
            RAISE EXCEPTION 'account role is immutable' USING ERRCODE = '23514';
        END IF;
        IF OLD.workshop_id IS NOT NULL AND (
            NEW.workshop_id IS DISTINCT FROM OLD.workshop_id
            OR NEW.workshop_role_id IS NULL
            OR NEW.onboarding_state IS NOT NULL
        ) THEN
            RAISE EXCEPTION 'attached user cannot detach or change workshop' USING ERRCODE = '23514';
        END IF;
    END IF;

    IF NEW.workshop_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT workshop_id, machine_key, status
      INTO role_workshop_id, role_machine_key, role_status
      FROM public.workshop_role
     WHERE id = NEW.workshop_role_id;
    IF NOT FOUND OR role_status <> 'active' THEN
        RAISE EXCEPTION 'workshop role is not assignable' USING ERRCODE = '23514';
    END IF;

    IF NEW.account_role = 'admin' THEN
        IF role_machine_key IS DISTINCT FROM 'admin' OR role_workshop_id IS NOT NULL THEN
            RAISE EXCEPTION 'attached admin must use Admin role' USING ERRCODE = '23514';
        END IF;
    ELSIF NOT (
        (role_machine_key = 'undefined' AND role_workshop_id IS NULL)
        OR (role_machine_key IS NULL AND role_workshop_id = NEW.workshop_id)
    ) THEN
        RAISE EXCEPTION 'workshop role is outside the user scope' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER cst_014_022_026_664_user_write_guard
BEFORE INSERT OR UPDATE ON public.user_account
FOR EACH ROW EXECUTE FUNCTION public.sb01_user_write_guard();

CREATE FUNCTION public.sb01_user_delete_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
    IF NOT (OLD.status = 'pending' AND OLD.account_role = 'manager') THEN
        RAISE EXCEPTION 'user cannot be deleted' USING ERRCODE = '23503';
    END IF;
    RETURN OLD;
END;
$function$;

CREATE TRIGGER cst_020_user_delete_guard
BEFORE DELETE ON public.user_account
FOR EACH ROW EXECUTE FUNCTION public.sb01_user_delete_guard();
"""


REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS cst_020_user_delete_guard ON public.user_account;
DROP FUNCTION IF EXISTS public.sb01_user_delete_guard();
DROP TRIGGER IF EXISTS cst_014_022_026_664_user_write_guard ON public.user_account;
DROP FUNCTION IF EXISTS public.sb01_user_write_guard();
ALTER TABLE public.user_account
DROP CONSTRAINT user_account_workshop_role_id_fb630c41_fk_workshop_role_id,
ADD CONSTRAINT user_account_workshop_role_id_fb630c41_fk_workshop_role_id
FOREIGN KEY (workshop_role_id) REFERENCES public.workshop_role(id)
DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public.user_account
DROP CONSTRAINT user_account_workshop_id_c527df3f_fk_workshop_id,
ADD CONSTRAINT user_account_workshop_id_c527df3f_fk_workshop_id
FOREIGN KEY (workshop_id) REFERENCES public.workshop(id)
DEFERRABLE INITIALLY DEFERRED;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0001_initial"),
        ("workshops", "0002_database_guards"),
    ]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
