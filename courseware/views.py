import json
import logging
import os
import sys
import StringIO
import urllib
import uuid

from django.conf import settings
from django.core.context_processors import csrf
from django.contrib.auth.models import User
from django.http import HttpResponse, Http404
from django.shortcuts import redirect
from django.template import Context, loader
from mitxmako.shortcuts import render_to_response, render_to_string
from django.db import connection

from lxml import etree

from auth.models import UserProfile
from models import StudentModule
from module_render import render_module, modx_dispatch
import courseware.content_parser as content_parser
import courseware.modules.capa_module

log = logging.getLogger("mitx.courseware")

etree.set_default_parser(etree.XMLParser(dtd_validation=False, load_dtd=False,
                                         remove_comments = True))

template_imports={'urllib':urllib}

def profile(request):
    ''' User profile. Show username, location, etc, as well as grades .
        We need to allow the user to change some of these settings .'''
    if not request.user.is_authenticated():
        return redirect('/')
    
    dom=content_parser.course_file(request.user)
    hw=[]
    course = dom.xpath('//course/@name')[0]
    chapters = dom.xpath('//course[@name=$course]/chapter', course=course)

    responses=StudentModule.objects.filter(student=request.user)
    response_by_id = {}
    for response in responses:
        response_by_id[response.module_id] = response
        
        
    total_scores = {}

    for c in chapters:
        chname=c.get('name')
        for s in dom.xpath('//course[@name=$course]/chapter[@name=$chname]/section', 
                           course=course, chname=chname):
            problems=dom.xpath('//course[@name=$course]/chapter[@name=$chname]/section[@name=$section]//problem', 
                           course=course, chname=chname, section=s.get('name'))
            scores=[]
            if len(problems)>0:
                for p in problems:
                    id = p.get('id')
                    correct = 0
                    if id in response_by_id:
                        response = response_by_id[id]
                        if response.grade!=None:
                            correct=response.grade
                    
                    total=courseware.modules.capa_module.LoncapaModule(etree.tostring(p), "id").max_score() # TODO: Add state. Not useful now, but maybe someday problems will have randomized max scores? 
                    scores.append((int(correct),total, ( True if s.get('graded') == "True" else False ) ))
                    
                    
                section_total = (sum([score[0] for score in scores]), 
                                sum([score[1] for score in scores]))
                
                graded_total = (sum([score[0] for score in scores if score[2]]), 
                                sum([score[1] for score in scores if score[2]]))
                
                #Add the graded total to total_scores
                format = s.get('format') if s.get('format') else ""
                if format and graded_total[1] > 0:
                    format_scores = total_scores[ format ] if format in total_scores else []
                    format_scores.append( graded_total )
                    total_scores[ format ] = format_scores
                
                score={'course':course,
                       'section':s.get("name"),
                       'chapter':c.get("name"),
                       'scores':scores,
                       'section_total' : section_total,
                       'format' : format,
                       }
                hw.append(score)
    
    
    
    def totalWithDrops(scores, drop_count):
        sorted_scores = sorted( enumerate(scores), key=lambda x: -x[1]['percentage'] ) #Note that this key will sort the list descending
        dropped_indices = [score[0] for score in sorted_scores[-drop_count:]] # A list of the indices of the dropped scores
        aggregate_score = 0
        for index, score in enumerate(scores):
            if index not in dropped_indices:
                aggregate_score += score['percentage']
        
        aggregate_score /= len(scores) - drop_count
        
        return aggregate_score, dropped_indices
        
    #Figure the homework scores
    homework_scores = total_scores['Homework'] if 'Homework' in total_scores else []
    homework_percentages = []
    for i in range(12):
        if i < len(homework_scores):
            percentage = homework_scores[i][0] / float(homework_scores[i][1])
            summary = "{:.0%} ({}/{})".format( percentage, homework_scores[i][0], homework_scores[i][1] )
        else:
            percentage = 0
            summary = "0% (?/?)"
        summary = "Homework {} - {}".format(i + 1, summary)
        
        homework_percentages.append( {'percentage': percentage, 'summary': summary} )
    homework_total, homework_dropped_indices = totalWithDrops(homework_percentages, 2)
    
    #Figure the lab scores
    lab_scores = total_scores['Lab'] if 'Lab' in total_scores else []
    lab_percentages = []
    for i in range(12):
        if i < len(lab_scores):
            percentage = lab_scores[i][0] / float(lab_scores[i][1])
            summary = "{:.0%} ({}/{})".format( percentage, lab_scores[i][0], lab_scores[i][1] )
        else:
            percentage = 0
            summary = "0% (?/?)"
        summary = "Lab {} - {}".format(i + 1, summary)
        lab_percentages.append( {'percentage': percentage, 'summary': summary} )
    lab_total, lab_dropped_indices = totalWithDrops(lab_percentages, 2)
    
    midterm_score = (120, 150)
    midterm_percentage = midterm_score[0] / float(midterm_score[1])
    
    final_score = (200, 300)
    final_percentage = final_score[0] / float(final_score[1])
    
    grade_summary = [
        {
            'category': 'Homework',
            'subscores' : homework_percentages,
            'dropped_indices' : homework_dropped_indices,
            'totalscore' : {'score' : homework_total, 'summary' : "Homework Average - {:.0%}".format(homework_total)},
            'weight' : 0.15,
        },
        {
            'category': 'Labs',
            'subscores' : lab_percentages,
            'dropped_indices' : lab_dropped_indices,
            'totalscore' : {'score' : lab_total, 'summary' : "Lab Average - {:.0%}".format(lab_total)},
            'weight' : 0.15,
        },
        {
            'category': 'Midterm',
            'totalscore' : {'score' : midterm_percentage, 'summary' : "Midterm - {:.0%} ({}/{})".format(midterm_percentage, midterm_score[0], midterm_score[1])},
            'weight' : 0.30,
        },
        {
            'category': 'Final',
            'totalscore' : {'score' : final_percentage, 'summary' : "Final - {:.0%} ({}/{})".format(final_percentage, final_score[0], final_score[1])},
            'weight' : 0.40,
        }
    ]
    
    
    user_info=UserProfile.objects.get(user=request.user)
    context={'name':user_info.name,
             'username':request.user.username,
             'location':user_info.location,
             'language':user_info.language,
             'email':request.user.email,
             'homeworks':hw,
             'grade_summary' : grade_summary,
             'csrf':csrf(request)['csrf_token']
             }
    return render_to_response('profile.html', context)

def render_accordion(request,course,chapter,section):
    ''' Draws navigation bar. Takes current position in accordion as
        parameter. Returns (initialization_javascript, content)'''
    def format_string(string):
        return urllib.quote(string.replace(' ','_'))

    toc=content_parser.toc_from_xml(content_parser.course_file(request.user), chapter, section)
    active_chapter=1
    for i in range(len(toc)):
        if toc[i]['active']:
            active_chapter=i
    context=dict([['active_chapter',active_chapter],
                  ['toc',toc], 
                  ['course_name',course],
                  ['format_string',format_string],
                  ['csrf',csrf(request)['csrf_token']]] + \
                     template_imports.items())
    return {'init_js':render_to_string('accordion_init.js',context), 
            'content':render_to_string('accordion.html',context)}

def index(request, course="6.002 Spring 2012", chapter="Using the System", section="Hints"): 
    ''' Displays courseware accordion, and any associated content. 
    ''' 
    user = request.user
    if not settings.COURSEWARE_ENABLED or not user.is_authenticated():
        return redirect('/')

    # Fixes URLs -- we don't get funny encoding characters from spaces
    # so they remain readable
    ## TODO: Properly replace underscores
    course=course.replace("_"," ")
    chapter=chapter.replace("_"," ")
    section=section.replace("_"," ")

    # HACK: Force course to 6.002 for now
    # Without this, URLs break
    if course!="6.002 Spring 2012":
        return redirect('/')

    dom = content_parser.course_file(user)
    dom_module = dom.xpath("//course[@name=$course]/chapter[@name=$chapter]//section[@name=$section]/*[1]", 
                           course=course, chapter=chapter, section=section)
    if len(dom_module) == 0:
        module = None
    else:
        module = dom_module[0]

    accordion=render_accordion(request, course, chapter, section)

    module_ids = dom.xpath("//course[@name=$course]/chapter[@name=$chapter]//section[@name=$section]//@id", 
                           course=course, chapter=chapter, section=section)

    module_object_preload = list(StudentModule.objects.filter(student=user, 
                                                              module_id__in=module_ids))
    

    module=render_module(user, request, module, module_object_preload)

    if 'init_js' not in module:
        module['init_js']=''

    context={'init':accordion['init_js']+module['init_js'],
             'accordion':accordion['content'],
             'content':module['content'],
             'csrf':csrf(request)['csrf_token']}

    result = render_to_response('courseware.html', context)
    return result
