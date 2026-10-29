def clear_role_cache(sender,instance,action,reverse,**kwargs):
    if action.startswith('post_') and not reverse:
        instance.__dict__.pop('_staff_group_names',None)
