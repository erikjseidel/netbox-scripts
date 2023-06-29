import yaml
from functools import wraps
from utilities.exceptions import AbortTransaction

class CancelScript(Exception):
    pass


def cancellable(func):
    """
    Cancellable scripts can be stopped and rolled back mid-execution by raising
    the CancelScript exception.

    The CancelScript exception is handled in a manner similar to AbortScript but
    with two differences:

    (1) script.output is set to the CancelScript message
    (2) JobStatusChoices.STATUS_ERRORED is not set so that script exits as though
        it were run with commit=False (but midstream as opposed to reaching end).

    This allows for a means to leave a script in a more "BAU" fashion in the case
    of data entry / form validation errors.
    """
    @wraps(func)
    def decorator(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)

        # Custom abort script handler. Add output rather rather than no output
        # (which is default action)
        except CancelScript as e:
            self.log_failure(f"Script cancelled with error: {e}")
            self.log_info("Database changes have been reverted due to error.")
            self.output = str(e)

            # Tell Netbox to rollback the transaction (same behaviour as commit=False)
            raise AbortTransaction()

    return decorator
