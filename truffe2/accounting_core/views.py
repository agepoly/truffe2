# -*- coding: utf-8 -*-

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import ugettext_lazy as _

from accounting_core import models as accounting_models
from app.utils import update_current_year
from generic.models import copiable_things


@login_required
def copy_accounting_year(request, pk):
    from accounting_core.models import AccountingYear, AccountCategory

    accounting_years = [get_object_or_404(AccountingYear, pk=pk_) for pk_ in filter(lambda x: x, pk.split(','))]

    for ay in accounting_years:
        if not ay.rights_can('EDIT', request.user):
            raise Http404

        copiable_objects = {}
        for copiable_class in copiable_things:
            copiable_objects[copiable_class] = getattr(ay, '{}_set'.format(copiable_class.__name__.lower())).all()

        ay.name = 'Copy of {}'.format(ay.name)
        ay.id = None
        ay.save()

        for cp_obj in copiable_objects.values():
            # Create the new objects
            for elem in cp_obj:
                elem.accounting_year = ay
                elem.id = None
                elem.save()

        for cp_class, cp_obj in copiable_objects.iteritems():
            # Correct dependencies on the new objects
            if hasattr(cp_class.MetaAccounting, 'foreign'):
                for (field_name, field_class) in cp_class.MetaAccounting.foreign:
                    for elem in cp_obj:
                        if getattr(elem, field_name):  # if it was None, remains None
                            setattr(elem, field_name, getattr(accounting_models, field_class).objects.get(accounting_year=ay, name=getattr(elem, field_name).name))
                        elem.save()

    messages.success(request, _(u'Copie terminée avec succès'))

    if len(accounting_years) == 1:
        update_current_year(request, accounting_years[0].pk)
        return redirect('accounting_core.views.accountingyear_edit', accounting_years[0].pk)
    else:
        return redirect('accounting_core.views.accountingyear_list')
