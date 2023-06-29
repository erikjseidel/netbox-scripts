import yaml
from functools import wraps

def yaml_out(func):
    """
    Decorator that converts dict formatted script output to yaml 

    In case of str out put a yaml dict is generated. Exception
    messages are made into yaml in case of exceptions.
    """
    @wraps(func)
    def decorator(self, *args, **kwargs):
        try:
            self.output = func(self, *args, **kwargs)

        except Exception as e:
            self.output = {
                    'result'  : False,
                    'comment' : self.output,
                    }

            raise e

        finally:
            if isinstance(self.output, dict):
                self.output = yaml.dump(self.output)
            elif isinstance(self.output, str):
                self.output = {
                        'result'  : True,
                        'comment' : self.output,
                        }

        return self.output
    return decorator
