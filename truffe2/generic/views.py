# -*- coding: utf-8 -*-

from django.shortcuts import get_object_or_404, render, redirect
from django.template import RequestContext
from django.core.context_processors import csrf
from django.views.decorators.csrf import csrf_exempt
from django.http import Http404, HttpResponse, HttpResponseForbidden, HttpResponseNotFound
from django.utils.encoding import smart_str
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.db import connections
from django.core.paginator import InvalidPage, EmptyPage, Paginator, PageNotAnInteger
from django.core.cache import cache
from django.core.urlresolvers import reverse
from django.contrib import messages
from django.utils.translation import ugettext_lazy as _
from django.utils.timezone import now
from django.db.models import Max, Q


import json
import datetime


from generic.datatables import generic_list_json
from generic.forms import ContactForm
from app.utils import update_current_unit, get_current_unit, send_templated_mail
from rights.utils import BasicRightModel


def get_unit_data(model_class, request, allow_blank=True):

    from generic.models import GenericExternalUnitAllowed

    unit_mode = hasattr(model_class.MetaData, 'has_unit') and model_class.MetaData.has_unit
    unit_blank = allow_blank and unit_mode and issubclass(model_class, GenericExternalUnitAllowed)

    current_unit = None

    if unit_mode:

        if request.GET.get('upk'):
            update_current_unit(request, request.GET.get('upk'))

        if request.POST.get('upk'):
            update_current_unit(request, request.POST.get('upk'))

        current_unit = get_current_unit(request, unit_blank)

    return unit_mode, current_unit, unit_blank


def generate_generic_list(module, base_name, model_class, json_view_suffix, right_to_check, right_to_check_edit, template_to_use, allow_blank, object_filter=False):

    @login_required
    def _generic_generic_list(request):

        json_view = module.__name__ + '.views.' + base_name + json_view_suffix
        edit_view = module.__name__ + '.views.' + base_name + '_edit'
        show_view = module.__name__ + '.views.' + base_name + '_show'
        deleted_view = module.__name__ + '.views.' + base_name + '_deleted'

        unit_mode, current_unit, unit_blank = get_unit_data(model_class, request, allow_blank=allow_blank)
        main_unit = None

        if unit_mode:
            from units.models import Unit

            main_unit = Unit.objects.get(pk=settings.ROOT_UNIT_PK)

            main_unit.set_rights_can_select(lambda unit: model_class.static_rights_can(right_to_check, request.user, unit))
            main_unit.set_rights_can_edit(lambda unit: model_class.static_rights_can(right_to_check_edit, request.user, unit))
        else:
            # The LIST right is not verified here if we're in unit mode. We
            # need to test (in the view) in another unit is available for LIST
            # if the current unit isn't !
            if hasattr(model_class, 'static_rights_can') and not model_class.static_rights_can(right_to_check, request.user, current_unit):
                raise Http404

        if hasattr(model_class, 'moderable_object') and model_class.moderable_object:  # If the object is moderable, list all moderable things by the current user
            # List all moderiables in the 'todo' satate
            moderables = model_class.objects.filter(status=model_class.moderable_state)

            # Filter to check if user has rights
            moderables = filter(lambda m: m.rights_can('VALIDATE', request.user), moderables)
        else:
            moderables = False

        if object_filter:
            objects = model_class.get_linked_object_class().objects.filter(unit=current_unit)
        else:
            objects = []

        return render(request, [module.__name__ + '/' + base_name + '/%s.html' % (template_to_use,), 'generic/generic/%s.html' % (template_to_use,)], {
            'Model': model_class, 'json_view': json_view, 'edit_view': edit_view, 'deleted_view': deleted_view, 'show_view': show_view,
            'unit_mode': unit_mode, 'main_unit': main_unit, 'unit_blank': unit_blank,
            'moderables': moderables, 'object_filter': objects,
        })

    return _generic_generic_list


def generate_list(module, base_name, model_class):

    return generate_generic_list(module, base_name, model_class, '_list_json', 'LIST', 'CREATE', 'list', True)


def generate_list_json(module, base_name, model_class):

    @login_required
    @csrf_exempt
    def _generic_list_json(request):
        show_view = module.__name__ + '.views.' + base_name + '_show'
        edit_view = module.__name__ + '.views.' + base_name + '_edit'
        delete_view = module.__name__ + '.views.' + base_name + '_delete'
        logs_view = module.__name__ + '.views.' + base_name + '_logs'

        unit_mode, current_unit, unit_blank = get_unit_data(model_class, request)

        if unit_mode:
            if not current_unit:
                if request.user.is_superuser:  # Never filter
                    filter_ = lambda x: x.filter(unit=None)
                else:
                    filter_ = lambda x: x.filter(unit=None, unit_blank_user=request.user)
            else:
                filter_ = lambda x: x.filter(unit=current_unit)
        else:
            filter_ = lambda x: x

        if hasattr(model_class, 'static_rights_can') and not model_class.static_rights_can('LIST', request.user, current_unit):
            raise Http404

        return generic_list_json(request, model_class, [col for (col, disp) in model_class.MetaData.list_display] + ['pk'], [module.__name__ + '/' + base_name + '/list_json.html', 'generic/generic/list_json.html'],
            {'Model': model_class,
             'show_view': show_view,
             'edit_view': edit_view,
             'delete_view': delete_view,
             'logs_view': logs_view
            },
            True, model_class.MetaData.filter_fields,
            bonus_filter_function=filter_
        )

    return _generic_list_json



def generate_list_related(module, base_name, model_class):

    return generate_generic_list(module, base_name, model_class, '_list_related_json', 'VALIDATE', 'VALIDATE', 'list_related', False, True)


def generate_list_related_json(module, base_name, model_class):

    @login_required
    @csrf_exempt
    def _generic_list_json(request):
        show_view = module.__name__ + '.views.' + base_name + '_show'
        edit_view = module.__name__ + '.views.' + base_name + '_edit'
        delete_view = module.__name__ + '.views.' + base_name + '_delete'
        logs_view = module.__name__ + '.views.' + base_name + '_logs'

        unit_mode, current_unit, unit_blank = get_unit_data(model_class, request, allow_blank=False)

        if unit_mode:
            filter_ = lambda x: x.filter(**{model_class.generic_state_unit_field.replace('.', '__'): current_unit})
        else:
            filter_ = lambda x: x

        def filter_object(qs, request):
            if request.POST.get('sSearch_0'):
                return qs.filter(**{'__'.join(model_class.generic_state_unit_field.split('.')[:-1] + ['pk']): request.POST.get('sSearch_0'), model_class.generic_state_unit_field.replace('.', '__'): current_unit})
            else:
                return qs

        if hasattr(model_class, 'static_rights_can') and not model_class.static_rights_can('VALIDATE', request.user, current_unit):
            raise Http404

        return generic_list_json(request, model_class, [model_class.generic_state_unit_field.split('.')[0]] + [col for (col, disp) in model_class.MetaData.list_display] + ['pk'], [module.__name__ + '/' + base_name + '/list_related_json.html', 'generic/generic/list_related_json.html'],
            {'Model': model_class,
             'show_view': show_view,
             'edit_view': edit_view,
             'delete_view': delete_view,
             'logs_view': logs_view
            },
            True, model_class.MetaData.filter_fields,
            bonus_filter_function=filter_,
            bonus_filter_function_with_parameters=filter_object
        )

    return _generic_list_json


def generate_edit(module, base_name, model_class, form_class, log_class):

    @login_required
    @csrf_exempt
    def _generic_edit(request, pk):
        list_view = module.__name__ + '.views.' + base_name + '_list'
        show_view = module.__name__ + '.views.' + base_name + '_show'

        unit_mode, current_unit, unit_blank = get_unit_data(model_class, request)

        try:
            obj = model_class.objects.get(pk=pk, deleted=False)

            if unit_mode:
                update_current_unit(request, obj.unit.pk if obj.unit else -1)
                current_unit = obj.unit

            if isinstance(obj, BasicRightModel) and not obj.rights_can('EDIT', request.user):
                raise Http404

        except (ValueError, model_class.DoesNotExist):
            obj = model_class()

            if unit_mode:
                if unit_blank and not current_unit:
                    obj.unit_blank_user = request.user
                obj.unit = current_unit

            if isinstance(obj, BasicRightModel) and not obj.rights_can('CREATE', request.user):
                raise Http404

        if unit_mode:
            from units.models import Unit

            main_unit = Unit.objects.get(pk=settings.ROOT_UNIT_PK)

            main_unit.set_rights_can_select(lambda unit: model_class.static_rights_can('CREATE', request.user, unit))
            main_unit.set_rights_can_edit(lambda unit: model_class.static_rights_can('CREATE', request.user, unit))
        else:
            main_unit = None

        if obj.pk:
            before_data = obj.build_state()
        else:
            before_data = None

        if request.method == 'POST':  # If the form has been submitted...
            form = form_class(request.user, request.POST, request.FILES, instance=obj)

            if form.is_valid():  # If the form is valid

                obj = form.save()

                if isinstance(obj, BasicRightModel):
                    obj.rights_expire()

                if hasattr(obj, 'save_signal'):
                    obj.save_signal()

                messages.success(request, _(u'Élément sauvegardé !'))

                if not before_data:
                    log_class(who=request.user, what='created', object=obj).save()
                else:
                    # Compute diff
                    after_data = obj.build_state()

                    for key in list(before_data)[::]:  # Be sure we're on a copy
                        if key in after_data and after_data[key] == before_data[key]:
                            del after_data[key]
                            del before_data[key]

                    added = {}
                    edited = {}
                    deleted = {}

                    for key in before_data:
                        if key not in after_data:
                            deleted[key] = before_data[key]
                        else:
                            if not after_data[key]:
                                deleted[key] = before_data[key]
                                del after_data[key]
                            elif before_data[key]:
                                edited[key] = (before_data[key], after_data[key])
                                del after_data[key]

                    added = after_data

                    diff = {'added': added, 'edited': edited, 'deleted': deleted}

                    log_class(who=request.user, what='edited', object=obj, extra_data=json.dumps(diff)).save()

                if request.POST.get('post-save-dest'):
                    if request.POST.get('post-save-dest') == 'new':
                        return redirect(module.__name__ + '.views.' + base_name + '_edit', pk='~')
                    else:
                        return redirect(module.__name__ + '.views.' + base_name + '_edit', pk=obj.pk)

                return redirect(module.__name__ + '.views.' + base_name + '_show', pk=obj.pk)
        else:
            form = form_class(request.user, instance=obj)

        return render(request, [module.__name__ + '/' + base_name + '/edit.html', 'generic/generic/edit.html'], {'Model': model_class, 'form': form, 'list_view': list_view, 'show_view': show_view, 'unit_mode': unit_mode, 'current_unit': current_unit, 'main_unit': main_unit, 'unit_blank': unit_mode})

    return _generic_edit


def generate_show(module, base_name, model_class, log_class):

    @login_required
    def _generic_show(request, pk):

        edit_view = module.__name__ + '.views.' + base_name + '_edit'
        delete_view = module.__name__ + '.views.' + base_name + '_delete'
        log_view = module.__name__ + '.views.' + base_name + '_log'
        list_view = module.__name__ + '.views.' + base_name + '_list'
        status_view = module.__name__ + '.views.' + base_name + '_switch_status'
        contact_view = module.__name__ + '.views.' + base_name + '_contact'

        obj = get_object_or_404(model_class, pk=pk, deleted=False)

        unit_mode, current_unit, unit_blank = get_unit_data(model_class, request)

        if unit_mode:
            update_current_unit(request, obj.unit.pk if obj.unit else -1)
            current_unit = obj.unit

        if isinstance(obj, BasicRightModel) and not obj.rights_can('SHOW', request.user):
            raise Http404

        rights = []

        if hasattr(model_class, 'MetaRights'):

            for key, info in model_class.MetaRights.rights.iteritems():
                rights.append((key, info, obj.rights_can(key, request.user)))

        log_entires = log_class.objects.filter(object=obj).order_by('-when').all()

        if hasattr(obj, 'contactables_groups'):
            contactables_groups = obj.contactables_groups()
        else:
            contactables_groups = None

        return render(request, [module.__name__ + '/' + base_name + '/show.html', 'generic/generic/show.html'], {
            'Model': model_class, 'delete_view': delete_view, 'edit_view': edit_view, 'log_view': log_view, 'list_view': list_view, 'status_view': status_view, 'contact_view': contact_view,
            'obj': obj, 'log_entires': log_entires,
            'rights': rights,
            'unit_mode': unit_mode, 'current_unit': current_unit,
            'contactables_groups': contactables_groups
        })

    return _generic_show


def generate_delete(module, base_name, model_class, log_class):

    @login_required
    def _generic_delete(request, pk):

        show_view = module.__name__ + '.views.' + base_name + '_show'
        list_view = module.__name__ + '.views.' + base_name + '_list'

        obj = get_object_or_404(model_class, pk=pk, deleted=False)

        unit_mode, current_unit, unit_blank = get_unit_data(model_class, request)

        if unit_mode:
            update_current_unit(request, obj.unit.pk if obj.unit else -1)

        if isinstance(obj, BasicRightModel) and not obj.rights_can('DELETE', request.user):
            raise Http404

        can_delete = True
        can_delete_message = ''

        if hasattr(obj, 'can_delete'):
            (can_delete, can_delete_message) = obj.can_delete()

        if can_delete and request.method == 'POST' and request.POST.get('do') == 'it':
            obj.deleted = True
            if hasattr(obj, 'delete_signal'):
                obj.delete_signal()
            obj.save()

            log_class(who=request.user, what='deleted', object=obj).save()

            messages.success(request, _(u'Élément supprimé !'))
            return redirect(list_view)

        return render(request, [module.__name__ + '/' + base_name + '/delete.html', 'generic/generic/delete.html'], {
            'Model': model_class, 'show_view': show_view, 'list_view': list_view,
            'obj': obj, 'can_delete': can_delete, 'can_delete_message': can_delete_message,
        })

    return _generic_delete


def generate_deleted(module, base_name, model_class, log_class):

    @login_required
    def _generic_deleted(request):

        list_view = module.__name__ + '.views.' + base_name + '_list'

        unit_mode, current_unit, unit_blank = get_unit_data(model_class, request)

        if hasattr(model_class, 'static_rights_can') and not model_class.static_rights_can('RESTORE', request.user, current_unit):
            raise Http404

        if unit_mode:
            from units.models import Unit

            main_unit = Unit.objects.get(pk=settings.ROOT_UNIT_PK)

            main_unit.set_rights_can_select(lambda unit: model_class.static_rights_can('RESTORE', request.user, unit))
            main_unit.set_rights_can_edit(lambda unit: model_class.static_rights_can('RESTORE', request.user, unit))
        else:
            main_unit = None

        if request.method == 'POST':
            obj = get_object_or_404(model_class, pk=request.POST.get('pk'), deleted=True)

            if unit_mode:
                update_current_unit(request, obj.unit.pk if obj.unit else -1)

            if isinstance(obj, BasicRightModel) and not obj.rights_can('RESTORE', request.user):
                raise Http404

            obj.deleted = False
            if hasattr(obj, 'restore_signal'):
                obj.restore_signal()
            obj.save()

            log_class(who=request.user, what='restored', object=obj).save()

            messages.success(request, _(u'Élément restauré !'))
            return redirect(list_view)

        liste = model_class.objects.filter(deleted=True).annotate(Max('logs__when')).order_by('-logs__when__max')

        if unit_mode:
            liste = liste.filter(unit=current_unit).all()
        else:
            liste = liste.all()

        return render(request, [module.__name__ + '/' + base_name + '/deleted.html', 'generic/generic/deleted.html'], {
            'Model': model_class, 'list_view': list_view, 'liste': liste,
            'unit_mode': unit_mode, 'current_unit': current_unit, 'main_unit': main_unit
        })

    return _generic_deleted


def generate_switch_status(module, base_name, model_class, log_class):

    @login_required
    def _switch_status(request, pk):

        obj = get_object_or_404(model_class, pk=pk, deleted=False)

        unit_mode, current_unit, unit_blank = get_unit_data(model_class, request)

        if unit_mode:
            update_current_unit(request, obj.unit.pk if obj.unit else -1)

        can_switch = True
        can_switch_message = ''
        done = False
        no_more_access = False
        status_view = module.__name__ + '.views.' + base_name + '_switch_status'
        list_view = module.__name__ + '.views.' + base_name + '_list'

        dest_status = request.GET.get('dest_status')

        if not hasattr(obj, 'MetaState') or dest_status not in obj.MetaState.states:
            raise Http404

        (can_switch, can_switch_message) = obj.can_switch_to(request.user, dest_status)

        bonus_form = None

        if hasattr(model_class.MetaState, 'states_bonus_form'):
            bonus_form = model_class.MetaState.states_bonus_form.get(dest_status, None)

        if can_switch and request.method == 'POST' and request.POST.get('do') == 'it':
            old_status = obj.status
            obj.status = dest_status
            obj.save()

            if isinstance(obj, BasicRightModel):
                obj.rights_expire()

            if hasattr(obj, 'switch_status_signal'):
                obj.switch_status_signal(request, old_status, dest_status)

            log_class(who=request.user, what='state_changed', object=obj, extra_data=json.dumps({'old': unicode(obj.MetaState.states.get(old_status)), 'new': unicode(obj.MetaState.states.get(dest_status))})).save()

            messages.success(request, _(u'Status modifié !'))
            done = True
            no_more_access = not obj.rights_can('SHOW', request.user)

            if no_more_access:
                messages.warning(request, _(u'Vous avez perdu le droit de voir l\'objet !'))

        return render(request, [module.__name__ + '/' + base_name + '/switch_status.html', 'generic/generic/switch_status.html'], {
            'Model': model_class, 'obj': obj, 'can_switch': can_switch, 'can_switch_message': can_switch_message, 'done': done, 'no_more_access': no_more_access,
            'dest_status': dest_status, 'dest_status_message': obj.MetaState.states.get(dest_status),
            'status_view': status_view, 'list_view': list_view,
            'bonus_form': bonus_form() if bonus_form else None,
        })

    return _switch_status


def generate_contact(module, base_name, model_class, log_class):

    @login_required
    def _contact(request, pk, key):

        contact_view = module.__name__ + '.views.' + base_name + '_contact'
        show_view = module.__name__ + '.views.' + base_name + '_show'

        obj = get_object_or_404(model_class, pk=pk, deleted=False)

        unit_mode, current_unit, unit_blank = get_unit_data(model_class, request)

        if unit_mode:
            update_current_unit(request, obj.unit.pk if obj.unit else -1)

        if isinstance(obj, BasicRightModel) and not obj.rights_can('SHOW', request.user):
            raise Http404

        if not hasattr(obj, 'contactables_groups'):
            raise Http404

        contactables_groups = obj.contactables_groups()

        done = False

        if request.method == 'POST':

            form = ContactForm(contactables_groups, request.POST)
            if form.is_valid():

                dest = [u.email for u in getattr(obj, 'build_group_members_for_%s' % (form.cleaned_data['key'],))()]

                context = {
                    'subject': form.cleaned_data['subject'],
                    'show_view': show_view,
                    'message': form.cleaned_data['message'],
                    'sender': request.user,
                    'obj': obj
                }

                send_templated_mail(request, _('Truffe :: Contact :: %s') % (form.cleaned_data['subject'],), request.user.email, dest, 'generic/generic/mail/contact', context)

                done = True
                messages.success(request, _(u'Message envoyé !'))
        else:
            form = ContactForm(contactables_groups, initial={'key': key})

        return render(request, [module.__name__ + '/' + base_name + '/contact.html', 'generic/generic/contact.html'], {
            'Model': model_class, 'obj': obj, 'contact_view': contact_view, 'form': form, 'done': done
        })

    return _contact


def check_unit_name(request):
    from units.models import Unit
    return HttpResponse(json.dumps({'result': 'ok' if Unit.objects.filter(name__icontains=request.GET.get('name')).count() == 0 else 'err'}))


def generate_calendar_list(module, base_name, model_class):

    return generate_generic_list(module, base_name, model_class, '_calendar_list_json', 'LIST', 'CREATE', 'calendar_list', True)


def generate_calendar_list_json(module, base_name, model_class):

    @login_required
    @csrf_exempt
    def _generic_calendar_list_json(request):

        unit_mode, current_unit, unit_blank = get_unit_data(model_class, request)

        if unit_mode:
            if not current_unit:
                if request.user.is_superuser:  # Never filter
                    filter_ = lambda x: x.filter(unit=None)
                else:
                    filter_ = lambda x: x.filter(unit=None, unit_blank_user=request.user)
            else:
                filter_ = lambda x: x.filter(unit=current_unit)
        else:
            filter_ = lambda x: x

        if hasattr(model_class, 'static_rights_can') and not model_class.static_rights_can('LIST', request.user, current_unit):
            raise Http404

        start = request.GET.get('start')
        end = request.GET.get('end')

        start = datetime.date.fromtimestamp(float(start))
        end = datetime.date.fromtimestamp(float(end))

        liste = filter_(model_class.objects.filter((Q(start_date__gt=start) & Q(start_date__lt=end)) | (Q(end_date__gt=start) & Q(end_date__lt=end))).filter(Q(status='1_asking') | Q(status='2_online')))

        retour = []

        for l in liste:
            # if l.unit:
            #     par = l.unit.name
            # else:
            #     par = '%s (%s)' % (l.unit_blank_name, l.unit_blank_user)

            if l.status == '1_asking':
                icon = 'fa-question'
                className = ["event", "bg-color-redLight"]
            else:
                icon = 'fa-check'
                className = ["event", "bg-color-greenLight"]

            if l.rights_can('SHOW', request.user):
                url = l.display_url()
            else:
                url = ''

            titre = '%s (%s)' % (l.get_linked_object(), l.get_linked_object().unit)

            retour.append({'title': titre, 'start': str(l.start_date), 'end': str(l.end_date), 'className': className, 'icon': icon, 'url': url, 'allDay': False, 'description': str(l)})

        return HttpResponse(json.dumps(retour))

    return _generic_calendar_list_json


def generate_calendar_list_related(module, base_name, model_class):

    return generate_generic_list(module, base_name, model_class, '_calendar_list_related_json', 'VALIDATE', 'VALIDATE', 'calendar_list_related', False, True)


def generate_calendar_list_related_json(module, base_name, model_class):

    @login_required
    @csrf_exempt
    def _generic_calendar_list_related_json(request):

        unit_mode, current_unit, unit_blank = get_unit_data(model_class, request, allow_blank=False)

        if unit_mode:
            filter_ = lambda x: x.filter(**{model_class.generic_state_unit_field.replace('.', '__'): current_unit})
        else:
            filter_ = lambda x: x

        if request.GET.get('filter_object'):
            filter__ = lambda x: x.filter(**{'__'.join(model_class.generic_state_unit_field.split('.')[:-1] + ['pk']): request.GET.get('filter_object'), model_class.generic_state_unit_field.replace('.', '__'): current_unit})
        else:
            filter__ = lambda x: x

        if hasattr(model_class, 'static_rights_can') and not model_class.static_rights_can('VALIDATE', request.user, current_unit):
            raise Http404

        start = request.GET.get('start')

        end = request.GET.get('end')

        start = datetime.date.fromtimestamp(float(start))
        end = datetime.date.fromtimestamp(float(end))

        liste = filter__(filter_(model_class.objects.filter((Q(start_date__gt=start) & Q(start_date__lt=end)) | (Q(end_date__gt=start) & Q(end_date__lt=end))).filter(Q(status='1_asking') | Q(status='2_online'))))

        retour = []

        for l in liste:
            if l.unit:
                par = l.unit.name
            else:
                par = '%s (%s)' % (l.unit_blank_name, l.unit_blank_user)

            if l.status == '1_asking':
                icon = 'fa-question'
                className = ["event", "bg-color-redLight"]
            else:
                icon = 'fa-check'
                className = ["event", "bg-color-greenLight"]

            if l.rights_can('SHOW', request.user):
                url = l.display_url()
            else:
                url = ''

            titre = '%s (%s)' % (l.get_linked_object(), par)

            retour.append({'title': titre, 'start': str(l.start_date), 'end': str(l.end_date), 'className': className, 'icon': icon, 'url': url, 'allDay': False, 'description': str(l)})

        return HttpResponse(json.dumps(retour))

    return _generic_calendar_list_related_json
