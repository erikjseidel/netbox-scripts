from extras.models import Tag

# KV delimiter used in semantic tags
DELIMITER='='

def lazy_load_tag(name):
    """
    Attempt to load a tag or create if does not already exist
    """

    if tag := Tag.objects.filter(name=name).first():
        return tag

    # Tag not found. Create a new one.
    tag = Tag(name=name)
    tag.save()

    return tag


def parse_semantic_tags(tags):
    """
    Returns a dictionary of parsed semantic tags. Regular (i.e. non-semantic)
    tags are ignored.
    """
    return {
            t.name.split(DELIMITER)[0] : t.name.split(DELIMITER)[1] 
            for t in tags if (DELIMITER in t.name)
            }


def make_semantic_tag(k, v):
    """
    Returns a  semantic tag for k and v in 'k=v' name format
    tags are ignored.
    """
    return lazy_load_tag(name=f'{k}{DELIMITER}{v}')
