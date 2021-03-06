# -*- coding: utf-8 -*-

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import Http404, HttpResponse
from django.utils.text import slugify
from django.utils.translation import ugettext_lazy as _
from django.shortcuts import get_object_or_404, redirect
from django.conf import settings
from django.contrib import messages


import datetime
import os
import json
from PIL import Image


from app.utils import generate_pdf


def get_statistics(subventions):
    """Given a set of subventions, return statistics about comms and assocs."""
    assoc = subventions.filter(unit=None, deleted=False).aggregate(Sum('amount_asked'), Sum('amount_given'), Sum('mobility_asked'), Sum('mobility_given'))
    comms = subventions.exclude(unit=None).filter(deleted=False).aggregate(Sum('amount_asked'), Sum('amount_given'), Sum('mobility_asked'), Sum('mobility_given'))

    return {'asso_amount_asked': assoc['amount_asked__sum'], 'asso_amount_given': assoc['amount_given__sum'],
            'asso_mobility_asked': assoc['mobility_asked__sum'], 'asso_mobility_given': assoc['mobility_given__sum'],
            'comm_amount_asked': comms['amount_asked__sum'], 'comm_amount_given': comms['amount_given__sum'],
            'comm_mobility_asked': comms['mobility_asked__sum'], 'comm_mobility_given': comms['mobility_given__sum']}


@login_required
def export_demands_yearly(request, ypk):
    from accounting_core.models import AccountingYear
    from accounting_tools.models import Subvention

    if not Subvention.static_rights_can('EXPORT', request.user):
        raise Http404

    try:
        ay = AccountingYear.objects.get(pk=ypk)
        subventions = Subvention.objects.filter(accounting_year=ay, deleted=False).order_by('unit__name', 'unit_blank_name')
        if subventions:
            subventions = list(subventions) + [get_statistics(subventions)]
        subv = [(ay.name, subventions)]
    except AccountingYear.DoesNotExist:
        subv = [(_(u'Période inconnue'), Subvention.objects.none())]

    return generate_pdf("accounting_tools/subvention/subventions_pdf.html", request, {'subventions': subv})


@login_required
def export_all_demands(request):
    from accounting_core.models import AccountingYear
    from accounting_tools.models import Subvention

    if not Subvention.static_rights_can('EXPORT', request.user):
        raise Http404

    years = AccountingYear.objects.filter(deleted=False).order_by('start_date')
    subventions = []
    for ay in years:
        subv = Subvention.objects.filter(accounting_year=ay, deleted=False).order_by('unit__name', 'unit_blank_name')
        if subv:
            subv = list(subv) + [get_statistics(subv)]
        subventions.append((ay.name, subv))

    summary = []
    units = sorted(list(set(map(lambda subv: subv.get_real_unit_name(), list(Subvention.objects.all())))))
    for unit_name in units:
        line = [unit_name]
        for year in years:
            year_subv = Subvention.objects.filter(accounting_year=year, deleted=False).filter(Q(unit__name=unit_name) | Q(unit_blank_name=unit_name)).first()
            if year_subv:
                line += [year_subv.amount_asked, year_subv.amount_given, year_subv.mobility_asked, year_subv.mobility_asked]
            else:
                line += ["", "", "", ""]
        summary.append(line)

    return generate_pdf("accounting_tools/subvention/subventions_pdf.html", request, {'subventions': subventions, 'summary': summary, 'years': years})


@login_required
def invoice_pdf(request, pk):

    from accounting_tools.models import Invoice

    invoice = get_object_or_404(Invoice, pk=pk, deleted=False)

    if not invoice.rights_can('DOWNLOAD_PDF', request.user):
        raise Http404

    img = invoice.generate_bvr()
    img = img.resize((1414, 1000), Image.LANCZOS)
    img.save(os.path.join(settings.MEDIA_ROOT, 'cache/bvr/{}.png').format(invoice.pk))

    return generate_pdf("accounting_tools/invoice/pdf.html", request, {'invoice': invoice})


@login_required
def invoice_bvr(request, pk):

    from accounting_tools.models import Invoice

    invoice = get_object_or_404(Invoice, pk=pk, deleted=False)

    if not invoice.rights_can('SHOW', request.user):
        raise Http404

    img = invoice.generate_bvr()

    response = HttpResponse(mimetype="image/png")
    img.save(response, 'png')
    return response


@login_required
def withdrawal_pdf(request, pk):

    from accounting_tools.models import Withdrawal

    withdrawal = get_object_or_404(Withdrawal, pk=pk, deleted=False)

    if not withdrawal.rights_can('SHOW', request.user):
        raise Http404

    return generate_pdf("accounting_tools/withdrawal/pdf.html", request, {'object': withdrawal})


@login_required
def internaltransfer_pdf(request, pk):
    from accounting_tools.models import InternalTransfer

    transfers = [get_object_or_404(InternalTransfer, pk=pk_, deleted=False) for pk_ in filter(lambda x: x, pk.split(','))]
    transfers = filter(lambda tr: tr.rights_can('SHOW', request.user), transfers)

    if not transfers:
        raise Http404
    elif len(transfers) == 1:
        return generate_pdf("accounting_tools/internaltransfer/single_pdf.html", request, {'object': transfers[0]})
    else:
        return generate_pdf("accounting_tools/internaltransfer/multiple_pdf.html", request, {'objects': transfers})


@login_required
def internaltransfer_csv(request, pk):
    from accounting_tools.models import InternalTransfer
    from app.utils import UnicodeCSVWriter

    transfers = [get_object_or_404(InternalTransfer, pk=pk_, deleted=False) for pk_ in filter(lambda x: x, pk.split(','))]
    transfers = filter(lambda tr: tr.rights_can('SHOW', request.user), transfers)

    response = HttpResponse(content_type='text/csv; charset=cp1252')
    if len(transfers) == 1:
        response['Content-Disposition'] = 'attachment; filename="Transfert Interne {0} .csv"'.format(slugify(unicode(transfers[0])))
    else:
        response['Content-Disposition'] = 'attachment; filename="transfers_internes_{0}.csv"'.format(datetime.date.today().strftime("%d-%m-%Y"))

    # L'écriture du csv permet l'import dans sage comme définit ici : https://onlinehelp.sageschweiz.ch/sage-start/fr-ch/content/technique/d%C3%A9finition%20de%20l%20interface.htm
    # La prise en compte des centre de cout souffre encore de problèmes du coté de sage

    writer = UnicodeCSVWriter(response)
    line_number = 1
    for transfer in transfers:
        if not transfer.status[0] in ['2', '3']:
            messages.warning(request, _(u'Internal Transfer {0} pas à l\'état à comptabiliser/en comptabilisation').format(unicode(transfer)))
            if len(transfers) == 1:
                return redirect('accounting_tools.views.internaltransfer_show', transfer.pk)
            else:
                return redirect('accounting_tools.views.internaltransfer_list')

        header_row = [u'0', line_number, transfer.transfert_date.strftime(u"%d.%m.%Y"), 100000 + transfer.pk, transfer.name, transfer.amount, transfer.amount, '', '', u'CHF', 0, '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', u'INT_TF#{0}'.format(unicode(transfer.pk)), '']
        debit_row = [u'1', '', '', '', '', '', '', '', '', '', '', '', u'1', line_number, transfer.account.account_number, u'CHF', u'{0} - Débit'.format(transfer.name), transfer.amount, '', 0, '', u'Non Soumis à la TVA', u'Débit', '', transfer.transfert_date.strftime(u"%d.%m.%Y"), 0, transfer.amount, transfer.amount, 0, u'INT_TF#{0}'.format(unicode(transfer.pk)), transfer.cost_center_from.account_number]
        credit_row = [u'2', '', '', '', '', '', '', '', '', '', '', '', u'2', line_number, transfer.account.account_number, u'CHF', u'{0} - Crédit'.format(transfer.name), transfer.amount, '', 0, '', u'Non Soumis à la TVA', u'Crédit', '', transfer.transfert_date.strftime(u"%d.%m.%Y"), 0, transfer.amount, transfer.amount, 0, u'INT_TF#{0}'.format(unicode(transfer.pk)), transfer.cost_center_to.account_number]
        writer.writerows([header_row, debit_row, credit_row])
        line_number = line_number + 1
    return response


@login_required
def expenseclaim_pdf(request, pk):
    from accounting_tools.models import ExpenseClaim

    expenseclaim = get_object_or_404(ExpenseClaim, pk=pk, deleted=False)

    if not expenseclaim.rights_can('SHOW', request.user):
        raise Http404

    return generate_pdf("accounting_tools/expenseclaim/pdf.html", request, {'object': expenseclaim}, [f.file for f in expenseclaim.get_pdf_files()])


@login_required
def expenseclaim_csv(request, pk):
    from accounting_tools.models import ExpenseClaim
    from accounting_tools.models import ExpenseClaimLine
    from app.utils import UnicodeCSVWriter

    expenseclaims = [get_object_or_404(ExpenseClaim, pk=pk_, deleted=False) for pk_ in filter(lambda x: x, pk.split(','))]

    response = HttpResponse(content_type='text/csv; charset=cp1252')
    if len(expenseclaims) == 1:
        response['Content-Disposition'] = 'attachment; filename="NDF - {0}.csv"'.format(slugify(unicode(expenseclaims[0])))
    else:
        response['Content-Disposition'] = 'attachment; filename="notes_de_frais_{0}.csv"'.format(datetime.date.today().strftime("%d-%m-%Y"))

    # L'écriture du csv permet l'import dans sage comme définit ici : https://onlinehelp.sageschweiz.ch/sage-start/fr-ch/content/technique/d%C3%A9finition%20de%20l%20interface.htm
    # La prise en compte des centre de cout souffre encore de problèmes du coté de sage

    provider_to_export = []
    writer = UnicodeCSVWriter(response)

    expenseclaim_count = 1
    for expenseclaim in expenseclaims:
        if not expenseclaim.rights_can('SHOW', request.user):
            raise Http404
        if not expenseclaim.status[0] in ['4', '5', '6']:
            messages.warning(request, _(u'NDF {0} pas à l\'état à comptabiliser/en comptabilisation').format(unicode(expenseclaim)))
            if len(expenseclaims) == 1:
                return redirect('accounting_tools.views.expenseclaim_show', expenseclaim.pk)
            else:
                return redirect('accounting_tools.views.expenseclaim_list')

        writer.writerow([u'0', expenseclaim_count, u'Crédit', 300000 + expenseclaim.pk, expenseclaim.logs.first().when.strftime(u"%d.%m.%Y"), expenseclaim.user.username, u'CHF', 0, u'2000', u'NDF - {0}'.format(unicode(expenseclaim)), expenseclaim.logs.first().when.strftime(u"%d.%m.%Y"), '', '', '', '', '', '', '', '', u'NDF#{0}'.format(unicode(expenseclaim.pk))])
        provider_to_export.append(expenseclaim.user)
        first = True
        line_count = 1
        firstline = ExpenseClaimLine()
        for line in expenseclaim.get_lines():
            if first:
                firstline = line
                first = False
                continue
            line_count = line_count + expenseclaim_line_write(writer, expenseclaim, line, line_count, False, expenseclaim_count)
        line_count = line_count + expenseclaim_line_write(writer, expenseclaim, firstline, line_count, True, expenseclaim_count)
        expenseclaim_count = expenseclaim_count + 1

        for provider in provider_to_export:
            address_lines = provider.adresse.splitlines()
            try:
                city = address_lines[1]
                zip = address_lines[1]
            except:
                zip = u'1015'
                city = u'Lausanne'

            address_complete = u''
            for adr in address_lines:
                address_complete += unicode(adr)

            writer.writerow([provider.first_name, provider.last_name, address_lines[0], city, zip, provider.username, provider.email, provider.nom_banque, provider.iban_ou_ccp, address_complete])
    return response


def expenseclaim_line_write(writer, expenseclaim, line, line_number, last_line, expenseclaim_number):
    from accounting_core.models import TVA

    tva = TVA()
    tva.value = line.tva

    if tva.value == 0.0:
        tva_string = u'Non soumis à la TVA'
        tva.code = ''
    else:
        tva_string = u'Soumis'
        tva.code = 'TOSET'

    row = [u'1', '', '', '', '', '', '', '', '', '', '', expenseclaim_number * 10000 + line_number, line_number, line.value, u'NDF - {0} - {1}'.format(unicode(expenseclaim), line.label), line.account.account_number, tva_string, tva.code, tva.value, u'OK', u'NDF#{0}'.format(unicode(expenseclaim.pk)), expenseclaim.costcenter.account_number]

    if last_line:  # la dernière écriture doit être de type 2
        row[0] = u'2'

    writer.writerow(row)

    return 1  # nombre de ligne écrite


@login_required
def cashbook_pdf(request, pk):
    from accounting_tools.models import CashBook

    cashbook = get_object_or_404(CashBook, pk=pk, deleted=False)

    if not cashbook.rights_can('SHOW', request.user):
        raise Http404

    return generate_pdf("accounting_tools/cashbook/pdf.html", request, {'object': cashbook}, [f.file for f in cashbook.get_pdf_files()])


@login_required
def cashbook_csv(request, pk):
    from accounting_tools.models import CashBook
    from accounting_tools.models import CashBookLine
    from app.utils import UnicodeCSVWriter

    cashbooks = [get_object_or_404(CashBook, pk=pk_, deleted=False) for pk_ in filter(lambda x: x, pk.split(','))]

    response = HttpResponse(content_type='text/csv; charset=cp1252')
    if len(cashbooks) == 1:
        response['Content-Disposition'] = 'attachment; filename="JDC - {0}.csv"'.format(slugify(unicode(cashbooks[0])))
    else:
        response['Content-Disposition'] = 'attachment; filename="journaux_de_caisse_{0}.csv"'.format(datetime.date.today().strftime("%d-%m-%Y"))

    # L'écriture du csv permet l'import dans sage comme définit ici : https://onlinehelp.sageschweiz.ch/sage-start/fr-ch/content/technique/d%C3%A9finition%20de%20l%20interface.htm
    # La prise en compte des centre de cout souffre encore de problèmes du coté de sage

    writer = UnicodeCSVWriter(response)

    cashbook_count = 1
    for cashbook in cashbooks:
        if not cashbook.rights_can('SHOW', request.user):
            raise Http404
        if not cashbook.status[0] in ['4', '5', '6']:
            messages.warning(request, _(u'JDC {0} pas à l\'état à comptabiliser/en comptabilisation').format(unicode(cashbook)))
            if len(cashbooks) == 1:
                return redirect('accounting_tools.views.cashbook_show', cashbook.pk)
            else:
                return redirect('accounting_tools.views.cashbook_list')
        if not cashbook.total_incomes() == cashbook.total_outcomes():
            messages.warning(request, _(u'JDC {0} pas a 0, merci de le mettre a 0').format(unicode(cashbook)))
            if len(cashbooks) == 1:
                return redirect('accounting_tools.views.cashbook_show', cashbook.pk)
            else:
                return redirect('accounting_tools.views.cashbook_list')

        writer.writerow([u'0', cashbook_count, cashbook.get_lines()[0].date.strftime(u"%d.%m.%Y"), 200000 + cashbook.pk, cashbook.name, cashbook.total_incomes(), cashbook.total_incomes(), '', '', u'CHF', 0, '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', u'CASHBOOK#{0}'.format(unicode(cashbook.pk))])
        first = True
        line_count = 1
        firstline = CashBookLine()
        for line in cashbook.get_lines():
            if first:
                firstline = line
                first = False
                continue
            line_count = line_count + cashbook_line_write(writer, cashbook, line, line_count, False, cashbook_count)
        line_count = line_count + cashbook_line_write(writer, cashbook, firstline, line_count, True, cashbook_count)
        cashbook_count = cashbook_count + 1
    return response


def cashbook_line_write(writer, cashbook, line, line_number, last_line, cashbook_number):
    from accounting_core.models import TVA

    initial_line_number = line_number

    if line.is_output:
        type = u'Débit'
    else:
        type = u'Crédit'

    tva = TVA()
    tva.value = line.tva

    if tva.value == 0.0:
        tva_string = u'Non soumis à la TVA'
        tva.code = ''
    else:
        tva_string = u'Soumis'
        tva.code = 'TOSET'

    row = [u'1', '', '', '', '', '', '', '', '', '', '', '', line_number, cashbook_number, line.account.account_number, u'CHF', u'{0} {1}'.format(cashbook.name, line.label), line.value_ttc, tva.code, tva.value, '', tva_string, type, '', line.date.strftime(u"%d.%m.%Y"), 0, line.value_ttc, line.value_ttc, 100, u'CASHBOOK#{0}'.format(unicode(cashbook.pk)), cashbook.costcenter.account_number]

    line_number = line_number + 1

    if last_line:  # la dernière écriture doit être de type 2
        row[0] = u'2'

    writer.writerow(row)
    return line_number - initial_line_number  # nombre de ligne écrite


@login_required
def get_withdrawal_infos(request, pk):
    from accounting_tools.models import Withdrawal

    withdrawal = get_object_or_404(Withdrawal, pk=pk, deleted=False)

    if not withdrawal.rights_can('SHOW', request.user):
        raise Http404

    return HttpResponse(json.dumps({'user_pk': withdrawal.user.pk, 'costcenter_pk': withdrawal.costcenter.pk, 'date': str(withdrawal.withdrawn_date)}), content_type='application/json')


@login_required
def withdrawal_available_list(request):
    """Return the list of available withdrawals for a given unit and year"""
    from accounting_tools.models import Withdrawal
    from accounting_core.models import AccountingYear
    from units.models import Unit

    withdrawals = Withdrawal.objects.filter(deleted=False, status="3_used").order_by('-withdrawn_date')

    if request.GET.get('upk'):
        unit = get_object_or_404(Unit, pk=request.GET.get('upk'))
        withdrawals = withdrawals.filter(costcenter__unit=unit)

    if request.GET.get('ypk'):
        accounting_year = get_object_or_404(AccountingYear, pk=request.GET.get('ypk'))
        withdrawals = withdrawals.filter(accounting_year=accounting_year)

    withdrawals = filter(lambda withdrawal: withdrawal.rights_can('SHOW', request.user), list(withdrawals))

    retour = {'data': [{'pk': withdrawal.pk, 'name': withdrawal.__unicode__(), 'used': withdrawal.status == '3_used'} for withdrawal in withdrawals]}

    return HttpResponse(json.dumps(retour), content_type='application/json')

@login_required
def provider_available_list(request):
    """Return the list of available accounts for a given year"""
    from accounting_tools.models import FinancialProvider

    providers = FinancialProvider.objects.filter(deleted=False).order_by('name')

    retour = {'data': [{'pk': provider.pk, 'name': provider.name, 'tva': provider.tva_number} for provider in providers]}

    return HttpResponse(json.dumps(retour), content_type='application/json')

@login_required
def provider_invoice_pdf(request, pk):
    from accounting_tools.models import ProviderInvoice

    invoice = get_object_or_404(ProviderInvoice, pk=pk, deleted=False)

    if not invoice.rights_can('SHOW', request.user):
        raise Http404

    return generate_pdf("accounting_tools/providerinvoice/pdf.html", request, {'object': invoice}, [f.file for f in invoice.get_pdf_files()])
