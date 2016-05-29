# -*- coding: utf-8 -*-

from django import forms
from django.utils.translation import ugettext_lazy as _


from accounting_core.models import AccountingYear


class ImportForm(forms.Form):

    year = forms.ModelChoiceField(label=_(u'L\'année comptable'), queryset=AccountingYear.objects.filter(deleted=False).exclude(status='3_archived'))
    file = forms.FileField(label=_(u'Le fichier avec la compta'))
    type = forms.ChoiceField(label=_(u'Le type de fichier'), choices=[
        ('tab_2016', _(u'Format TAB 2016')),
        ('csv_2014', _(u'Format CSV 2014')),
    ])


class BudgetFilterForm(forms.Form):
    start = forms.DateField(label=_(u'Filter du'))
    end = forms.DateField(label=_(u'au'))
