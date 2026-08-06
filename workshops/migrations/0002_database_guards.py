from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE public.operation_type
DROP CONSTRAINT operation_type_workshop_id_154ef1da_fk_workshop_id,
ADD CONSTRAINT operation_type_workshop_id_154ef1da_fk_workshop_id
FOREIGN KEY (workshop_id) REFERENCES public.workshop(id) ON DELETE RESTRICT;

ALTER TABLE public.workshop_role
DROP CONSTRAINT workshop_role_workshop_id_00d6a754_fk_workshop_id,
ADD CONSTRAINT workshop_role_workshop_id_00d6a754_fk_workshop_id
FOREIGN KEY (workshop_id) REFERENCES public.workshop(id) ON DELETE RESTRICT;

CREATE FUNCTION public.sb01_workshop_lifecycle_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status
       AND NOT (
           (OLD.status = 'manager_required' AND NEW.status = 'manager_activation_pending')
           OR (OLD.status = 'manager_activation_pending' AND NEW.status = 'operational')
       ) THEN
        RAISE EXCEPTION 'invalid workshop lifecycle transition' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER cst_002_workshop_lifecycle
BEFORE UPDATE ON public.workshop
FOR EACH ROW EXECUTE FUNCTION public.sb01_workshop_lifecycle_guard();

CREATE FUNCTION public.sb01_workshop_role_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'workshop roles cannot be deleted' USING ERRCODE = '23503';
    END IF;

    IF NEW.workshop_id IS NULL THEN
        IF NOT (
            (NEW.machine_key = 'undefined' AND NEW.name = 'undefined' AND NEW.status = 'active')
            OR (NEW.machine_key = 'admin' AND NEW.name = 'Admin' AND NEW.status = 'active')
        ) THEN
            RAISE EXCEPTION 'invalid global workshop role identity' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.machine_key IS NOT NULL THEN
        RAISE EXCEPTION 'workshop-owned roles cannot have a machine key' USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'UPDATE' AND OLD.machine_key IS NOT NULL AND (
        NEW.machine_key IS DISTINCT FROM OLD.machine_key
        OR NEW.name IS DISTINCT FROM OLD.name
        OR NEW.workshop_id IS DISTINCT FROM OLD.workshop_id
        OR NEW.status IS DISTINCT FROM OLD.status
    ) THEN
        RAISE EXCEPTION 'protected workshop role identity is immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER cst_012_013_workshop_role_guard
BEFORE INSERT OR UPDATE ON public.workshop_role
FOR EACH ROW EXECUTE FUNCTION public.sb01_workshop_role_guard();

CREATE TRIGGER cst_workshop_role_no_delete
BEFORE DELETE ON public.workshop_role
FOR EACH ROW EXECUTE FUNCTION public.sb01_workshop_role_guard();

CREATE FUNCTION public.sb01_operation_type_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'operation types cannot be deleted' USING ERRCODE = '23503';
    END IF;

    IF NEW.workshop_id IS NULL THEN
        IF NOT (
            NEW.machine_key = 'other'
            AND NEW.name = 'Other'
            AND NEW.status = 'active'
            AND NEW.is_production
            AND NOT NEW.requires_clearance
        ) THEN
            RAISE EXCEPTION 'invalid global operation type identity' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.machine_key IS NULL THEN
        NULL;
    ELSIF NEW.machine_key = 'build_planning' THEN
        IF NOT (
            NEW.name = 'Build Planning'
            AND NEW.status = 'active'
            AND NOT NEW.is_production
            AND NEW.requires_clearance
        ) THEN
            RAISE EXCEPTION 'invalid protected operation type identity' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.machine_key = 'station_maintenance' THEN
        IF NOT (
            NEW.name = 'Station Maintenance'
            AND NEW.status = 'active'
            AND NOT NEW.is_production
            AND NEW.requires_clearance
        ) THEN
            RAISE EXCEPTION 'invalid protected operation type identity' USING ERRCODE = '23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'invalid operation type machine key' USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'UPDATE' AND OLD.machine_key IS NULL AND NEW.machine_key IS NOT NULL THEN
        RAISE EXCEPTION 'operation type protection cannot be added by update' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.machine_key IS NOT NULL AND (
        NEW.machine_key IS DISTINCT FROM OLD.machine_key
        OR NEW.name IS DISTINCT FROM OLD.name
        OR NEW.workshop_id IS DISTINCT FROM OLD.workshop_id
        OR NEW.status IS DISTINCT FROM OLD.status
        OR NEW.is_production IS DISTINCT FROM OLD.is_production
        OR NEW.requires_clearance IS DISTINCT FROM OLD.requires_clearance
    ) THEN
        RAISE EXCEPTION 'protected operation type identity is immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER cst_046_operation_type_guard
BEFORE INSERT OR UPDATE ON public.operation_type
FOR EACH ROW EXECUTE FUNCTION public.sb01_operation_type_guard();

CREATE TRIGGER cst_operation_type_no_delete
BEFORE DELETE ON public.operation_type
FOR EACH ROW EXECUTE FUNCTION public.sb01_operation_type_guard();
"""


REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS cst_operation_type_no_delete ON public.operation_type;
DROP TRIGGER IF EXISTS cst_046_operation_type_guard ON public.operation_type;
DROP FUNCTION IF EXISTS public.sb01_operation_type_guard();
DROP TRIGGER IF EXISTS cst_workshop_role_no_delete ON public.workshop_role;
DROP TRIGGER IF EXISTS cst_012_013_workshop_role_guard ON public.workshop_role;
DROP FUNCTION IF EXISTS public.sb01_workshop_role_guard();
DROP TRIGGER IF EXISTS cst_002_workshop_lifecycle ON public.workshop;
DROP FUNCTION IF EXISTS public.sb01_workshop_lifecycle_guard();
ALTER TABLE public.workshop_role
DROP CONSTRAINT workshop_role_workshop_id_00d6a754_fk_workshop_id,
ADD CONSTRAINT workshop_role_workshop_id_00d6a754_fk_workshop_id
FOREIGN KEY (workshop_id) REFERENCES public.workshop(id)
DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public.operation_type
DROP CONSTRAINT operation_type_workshop_id_154ef1da_fk_workshop_id,
ADD CONSTRAINT operation_type_workshop_id_154ef1da_fk_workshop_id
FOREIGN KEY (workshop_id) REFERENCES public.workshop(id)
DEFERRABLE INITIALLY DEFERRED;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("workshops", "0001_initial"),
        ("identity", "0001_initial"),
    ]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
