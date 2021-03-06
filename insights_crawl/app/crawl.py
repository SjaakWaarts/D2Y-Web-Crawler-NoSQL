﻿
from datetime import datetime
from datetime import time
from datetime import timedelta
from django.core.files import File
import glob, os
import sys
import pickle
import urllib
import requests
import json
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
import re
from requests_ntlm import HttpNtlmAuth
from pandas import Series, DataFrame
import pandas as pd
from bs4 import BeautifulSoup

from elasticsearch import Elasticsearch
from elasticsearch_dsl import Search, Q
from elasticsearch_dsl.connections import connections
from elasticsearch.client import IndicesClient
from elasticsearch.helpers import bulk

import app.models as models
import app.elastic as elastic
import app.survey as survey
from insights_crawl.settings import BASE_DIR, ES_HOSTS

si_sites = {
    'gci'   : {
        'site_url'  : 'http://www.gcimagazine.com/',
        'sub_sites' : {
            'gci'   : 'http://www.gcimagazine.com/'},
        },
    }

driver = None

class Crawler:
    site_name = ''
    pages = set()
    bulk_data = []
    nrpages = 50

    def __init__(self, site, nrpages):
        self.site = site
        self.nrpages = nrpages

    # read the content of a page into BeautifulSoup
    def read_page(self, url):
        try:
            print("read_page: scraping url ", url)
            html = urllib.request.urlopen(url)
            bs = BeautifulSoup(html.read(), "lxml")
            [script.decompose() for script in bs("script")]
        except:
            print("Scrape: could not open url ", url)
        return bs

    # Step though all summery pages (Next, Pagination) and from each summary page get all the link refering to the articles
    def get_pagination_links(self, sub_site):
        include_url = urlparse(sub_site).scheme+"://"+urlparse(sub_site).netloc
        links = set()
        url = sub_site
        page_nr = 0
        page_size = 10
        link_count = 1
        links.add(sub_site)
        return links

    # get all the links that point within this site
    def get_internal_links(self, url, bs):
        include_url = urlparse(url).scheme+"://"+urlparse(url).netloc
        links = set()
        link_count = 0
        for link_tag in bs.findAll("a", href=re.compile("^(/|.*"+include_url+")")) and link_count < self.nrpages:
            if link_tag.attrs['href'] is not None:
                if link_tag.attrs['href'] not in links:
                    if link_tag.attrs['href'].startswith('/'):
                        link = include_url+link_tag.attrs['href']
                    else:
                        link = link_tag.attrs['href']
                    links.add(link)
                    link_count = link_count + 1
        return links

    # get all the links that point outside this site
    def get_external_links(self, url, bs):
        include_url = urlparse(url).scheme+"://"+urlparse(url).netloc
        links = set()
        for link_tag in bs.findAll("a", href=re.compile("^(/|.*"+include_url+")")):
            if link_tag.attrs['href'] is not None:
                if link_tag.attrs['href'] not in links:
                    if link_tag.attrs['href'].startswith('/'):
                        links.append(include_url+link_tag.attrs['href'])
                    else:
                        link_tag.append(link.attrs['href'])
        return links

    # for this page (url) scrape its context and map this to the elasticsearch record (pagemap)
    def scrape_page_map(self, sub_site, url, bs):
        id = url
        site_url = urlparse(url).netloc.split('.')[1]
        sub_site_url = urlparse(url).path.split('/')
        sub_site_name = '-'.join(sub_site[1:-1])
        if sub_site_name == '':
            sub_site_name = 'Home'
        pagemap             = models.PageMap()

        pagemap.page_id     = id
        pagemap.site        = self.site
        pagemap.sub_site    = sub_site
        pagemap.url         = url
        pagemap.section     = ''

        # get posted date
        try:
            pagemap.posted_date = datetime.today()
        except:
            pass

        # get page
        try:
            pagemap.page        = bs.get_text()
        except:
            pass

        # get title
        try:
            if bs.title != None:
                pagemap.title   = bs.title.text
            else:
                pagemap.title   = ''
        except:
            pass

        data = elastic.convert_for_bulk(pagemap, 'update')
        return data

def crawl_si_site(site_choice, nrpages):
    crawler = Crawler (site_choice, nrpages)
    si_site = si_sites[site_choice]
    sub_sites = si_site.sub_sites
    site_url = si_site.site_url
           
    for sub_site, sub_site_url in sub_sites.items():
        bs = crawler.read_page(sub_site_url)
        links = crawler.get_internal_links(sub_site_url, bs)        
        for link in links:
             bs = crawler.read_page(link)
             apf.pages.add(link)
             data = apf.scrape_page_map(sub_site, link, bs)
             apf.bulk_data.append(data)
    
    bulk(models.client, actions=apf.bulk_data, stats_only=True)


#****************************** APF Crawler *****************************************

class AFPCrawler(Crawler):

    def get_pagination_links(self, sub_site):
        include_url = urlparse(sub_site).scheme+"://"+urlparse(sub_site).netloc
        links = set()
        url = sub_site
        page_nr = 0
        page_size = 10
        link_count = 0
        while url != None and link_count < self.nrpages:
            bs = self.read_page(url)
            blog_posts_tag = bs.find("div", class_="blog-posts")
            for link_tag in blog_posts_tag.findAll("a", href=re.compile("^(/|.*"+include_url+")")):
                if link_tag.attrs['href'] is not None:
                    if link_tag.attrs['href'] not in links:
                        if link_tag.attrs['href'].startswith('/'):
                            link = include_url+link_tag.attrs['href']
                        else:
                            link = link_tag.attrs['href']
                        links.add(link)
                        link_count = link_count + 1
            navigation_tag = bs.find("nav", class_="nav-below")
            if navigation_tag != None:
                next_tag = navigation_tag.find("span", class_="nav-next")
                if next_tag != None:
                    next_url = next_tag.parent.attrs['href']
                else:
                    next_url = None
            url = next_url
        return links


    def scrape_page_map(self, sub_site, url, bs):
        id = url
        pagemap             = models.PageMap()
        pagemap.page_id     = id
        pagemap.site        = self.site
        pagemap.sub_site    = sub_site
        pagemap.url         = url

        # get posted date
        # <span class="entry-date">May 23, 2017</span>
        try:
            pagemap.posted_date = datetime.today()
            entry_date_tag = bs.find("span", class_="entry-date")
            published = entry_date_tag.text
            pagemap.posted_date = datetime.strptime(published, '%B %d, %Y')
        except:
            pass
        #try:
        #    box_1_tag = bs.find("div", class_="box_1")
        #    product_info_bar_tag = box_1_tag.find("div", class_="product_info_bar")
        #    published = re.search(r'([0-9]{2}-[a-z,A-Z]{3}-[0-9]{4})', product_info_bar.text, re.MULTILINE)
        #    pagemap.posted_date = datetime.strptime(published.group(0), '%d-%b-%Y')
        #except:
        #    pass

        # get page
        # <section class="entry-content">
        try:
            pagemap.page = bs.get_text()
            entry_content_tag = bs.find("section", class_="entry-content")
            pagemap.page = entry_content_tag.text
        except:
            pass
        # get title
        # <h1 class="entry-title"></h1>  text
        try:
            if bs.title != None:
                pagemap.title   = bs.title.text
            else:
                pagemap.title   = ''
            entry_title_tag = bs.find("h1", class_="entry-title")
            pagemap.title = entry_title_tag.text
        except:
            pass
        # get section
        try:
            pagemap.section = sub_site
        except:
            pass

        data = elastic.convert_for_bulk(pagemap, 'update')
        return data


def crawl_apf(scrape_choices, nrpages):
    apf = AFPCrawler ('APF', nrpages)
    sub_sites = {}
    site_url = 'https://apf.org/'
    for scrape_choice in scrape_choices:
        if scrape_choice == 'blog':
            sub_sites['blog'] = site_url + '/blog'
        if scrape_choice == 'publications':
            sub_sites['blog'] = site_url + '/publications'
           
    for sub_site, sub_site_url in sub_sites.items():
        links = apf.get_pagination_links(sub_site_url)        
        for link in links:
             bs = apf.read_page(link)
             apf.pages.add(link)
             data = apf.scrape_page_map(sub_site, link, bs)
             apf.bulk_data.append(data)
    
    bulk(models.client, actions=apf.bulk_data, stats_only=True)


#****************************** Cosmetics Crawler *****************************************

class CosmeticsCrawler(Crawler):

    def get_pagination_links(self, sub_site):
        include_url = urlparse(sub_site).scheme+"://"+urlparse(sub_site).netloc
        links = set()
        url = sub_site
        page_nr = 0
        page_size = 10
        link_count = 0
        while url != None and link_count < self.nrpages:
            bs = self.read_page(url)
            box_1_tag = bs.find("div", class_="box_1")
            for link_tag in box_1_tag.findAll("a", href=re.compile("^(/|.*"+include_url+")")):
                if link_tag.attrs['href'] is not None:
                    if link_tag.attrs['href'] not in links:
                        if link_tag.attrs['href'].startswith('/'):
                            link = include_url+link_tag.attrs['href']
                        else:
                            link = link_tag.attrs['href']
                        links.add(link)
                        link_count = link_count + 1
            result_count_tag = bs.find("span", class_="result_count")
            if result_count_tag != None:
                result_count_list = result_count_tag.text.split()
                result_count = int(float(result_count_list[4]))
            else:
                result_count = page_size
            navigation_tag = bs.find(id="navigation")
            if navigation_tag != None:
                next_tag = navigation_tag.find("span", class_="next")
                if next_tag != None:
                    next_url = include_url + next_tag.find("a").attrs['href']
                else:
                    next_url = None
            else:
                page_nr = page_nr + 1
                if page_nr * page_size > result_count:
                    next_url = None
                else:
                    next_url = sub_site + '/(offset)/{}'.format(page_nr)
            url = next_url
        return links


    def scrape_page_map(self, sub_site, url, bs):
        id = url
        pagemap             = models.PageMap()
        pagemap.page_id     = id
        pagemap.site        = self.site
        pagemap.sub_site    = sub_site
        pagemap.url         = url

        # get posted date
        try:
            pagemap.posted_date = datetime.today()
            author_info_tag = bs.find("div", class_="author_info")
            published = author_info_tag.find('p', class_='date').text
            pagemap.posted_date = datetime.strptime(published, '%d-%b-%Y')
        except:
            pass
        try:
            box_1_tag = bs.find("div", class_="box_1")
            product_info_bar_tag = box_1_tag.find("div", class_="product_info_bar")
            published = re.search(r'([0-9]{2}-[a-z,A-Z]{3}-[0-9]{4})', product_info_bar.text, re.MULTILINE)
            pagemap.posted_date = datetime.strptime(published.group(0), '%d-%b-%Y')
        except:
            pass
        # get page
        try:
            pagemap.page        = bs.get_text()
            box_1_tag = bs.find("div", class_="box_1")
            pagemap.page = box_1_tag.text
            product_main_text_tag = box_1_tag.find("div", class_="product_main_text")
            if product_main_text_tag != None:
                pagemap.page = product_main_text_tag.text
            else:
                story_tag = box_1_tag.find("div", class_="story")
                pagemap.page = story_tag.text
        except:
            pass
        # get title
        try:
            if bs.title != None:
                pagemap.title   = bs.title.text
            else:
                pagemap.title   = ''
            box_1_tag = bs.find("div", class_="box_1")
            pagemap.title = box_1_tag.find("h1").text
        except:
            pass
        # get section
        try:
            box_2_tag = bs.find("div", class_="box_2")
            pagemap.section = box_2_tag.text.strip(' \t\n\r')
        except:
            pass

        data = elastic.convert_for_bulk(pagemap, 'update')
        return data


def crawl_cosmetic(scrape_choices, nrpages):
    cosmetic = CosmeticsCrawler('Cosmetics', nrpages)
    sub_sites = {}
    if len(scrape_choices) == 0:
        sub_sites.add(site)
#   for site in ['http://www.cosmeticsdesign.com/', 'http://www.cosmeticsdesign-europe.com/', 'http://www.cosmeticsdesign-asia.com/']:
    for site_url in ['http://www.cosmeticsdesign.com/']:
        for scrape_choice in scrape_choices:
            if scrape_choice == 'product':
                sub_sites['Skin-care'] = site_url + '/Product-Categories/Skin-Care'
                sub_sites['Hair-care'] = site_url +'/Product-Categories/Hair-Care'
            if scrape_choice == 'market':
                sub_sites['Market-Trends'] = site_url + '/Market-Trends'
                sub_sites['Brand-Innovation']= site_url +'/Brand-Innovation'

    for sub_site, sub_site_url in sub_sites.items():
        links = cosmetic.get_pagination_links(sub_site_url)
        for link in links:
            bs = cosmetic.read_page(link)
            cosmetic.pages.add(link)
            data = cosmetic.scrape_page_map(sub_site, link, bs)
            cosmetic.bulk_data.append(data)

    bulk(models.client, actions=cosmetic.bulk_data, stats_only=True)

#
# FEEDLY
#

def crawl_feedly(from_date, rss_field):
    global headers

    bulk_data = []
    today = datetime.now()
    days = timedelta(days=31)
    yesterday = today - days
    s = yesterday.timestamp()
    t = time(0, 0)
    dt = datetime.combine(from_date, t)
    s = dt.timestamp()
    #datetime.datetime.fromtimestamp(s).strftime('%c')
    ms = s * 1000
    newerthan = "{:.0f}".format(ms)
    headers = {
        #sjaak.waarts@gmail.com (expires on 2017-07-20)
        "Authorization" : "A2JxorrfeTBQbMUsDIU3_zexSwY8191e3P9EvewYowjfbhKwOgHk84ErlXAWXpucZ_McfTDHLZN6yLxWqxgjWM8Upp1c-6Nb_RpZd0jWA9mJkVLN1JTETefaVNZtZqzTGTf8_qeT2ZE8z6Bf4LqLOUfQaQH2-jj8XIaxAyWMZ5BDRtfpgwVYrEEM2ii5KXnMJZxGNEvcqAV4Dke_subaM-wlnC8N63g:feedlydev"
        }

    params_streams = {
#       "count"     : "100",
        "count"     : "1000",
        "ranked"    : "newest",
        "unreadOnly": "false",
        "newerThan" : newerthan
        }
    #url = "http://cloud.feedly.com/v3/profile"
    #r = requests.get(url, headers=headers)
    url = "http://cloud.feedly.com/v3/subscriptions"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return False
    feeds = r.json()
    for feed in feeds:
        feed_id = feed['id']
        feed_title = feed['title'].encode("ascii", 'replace')
        feed_category = feed['categories'][0]['label']
        print("crawl_feedly: scraping feed category/title", feed_category, feed_title)
        if rss_field == '' or feed_category == rss_field:
            url = "http://cloud.feedly.com/v3/streams/contents"
            params_streams['streamId'] = feed_id
            r = requests.get(url, headers=headers, params=params_streams)
            stream = r.json()
            if 'items' in stream:
                for entry in stream['items']:
                    feedlymap = models.FeedlyMap()
                    feedlymap.post_id = entry['id']
                    try:
                        feedlymap.published_date = datetime.fromtimestamp(entry['published']/1000)
                    except:
                        feedlymap.published_date = datetime(2010, 1, 1, 00, 00, 00)
                    feedlymap.category = feed_category
                    feedlymap.feed = feed_title
                    if 'topics' in feed:
                        feedlymap.feed_topics = feed['topics']
                    if 'keywords' in entry:
                        feedlymap.body_topics = entry['keywords']
                    feedlymap.title = entry['title']
                    if 'canonicalUrl' in entry:
                        feedlymap.url = entry['canonicalUrl']
                    else:
                        n = entry['originId'].find('http')
                        feedlymap.url = entry['originId'][n:]
                    feedlymap.post_id = feedlymap.url
                    if 'summary' in entry:
                        bs = BeautifulSoup(entry['summary']['content'],  "lxml") # in case of RSS feed
                    if 'content' in entry:
                        bs = BeautifulSoup(entry['content']['content'], "lxml") # in case of Google News feed
                    feedlymap.body = bs.get_text().encode("ascii", 'replace')
                    data = elastic.convert_for_bulk(feedlymap, 'update')
                    bulk_data.append(data)

    bulk(models.client, actions=bulk_data, stats_only=True)
    return True

def export_opml_feedly(opml_filename):
    global headers

    url = "http://cloud.feedly.com/v3/opml"
    r = requests.get(url, headers=headers)
    xml = r.content

    opml_file = 'data/' + opml_filename + '_opml.txt'
    try:
        file = open(opml_file, 'wb')
        pyfile = File(file)
        pyfile.write(xml)
        pyfile.close()
        return True
    except:
        return False


def import_opml_feedly(opml_filename):
    global headers

    opml_file = 'data/' + opml_filename + '_opml.txt'
    try:
        file = open(opml_file, 'rb')
        pyfile = File(file)
        xml = pyfile.read()
        pyfile.close()
    except:
        return False

    url = "http://cloud.feedly.com/v3/opml"
    h2 = headers
    h2['Content-Type'] = 'application/xml'
    r = requests.post(url, headers=headers, data=xml)
    
    return True


def crawl_studies_facts(survey_field, facts_d):
    bulk_data = []
    count = 0
    total_count = 0
    facts_df = DataFrame.from_dict(facts_d, orient='index')
    facts_df['blindcode'] = [ix[0] for ix in facts_df.index]
    facts_df['fact'] = [ix[1] for ix in facts_df.index]
    facts_df['answer'] = [ix[2] for ix in facts_df.index]

    for blindcode, facts_blindcode_df in facts_df.groupby(facts_df['blindcode']):
        se = models.StudiesMap()
        se.cft_id = blindcode
        se.dataset = survey_field
        se.ingr_name = blindcode
        se.IPC = blindcode
        percentile = {}

        for idx, fact_s in facts_blindcode_df.iterrows():
            fact = fact_s['fact']
            answer = fact_s['answer']
            #se.supplier = "CI"
            #se.olfactive = cft_s.olfactive
            #se.region = cft_s.region
            #se.review = cft_s.review
            #se.dilution = cft_s.dilution
            #se.intensity = cft_s.intensity

            if fact not in percentile.keys():
                percentile[fact] = []
            val = answer
            prc = fact_s[0]
            if prc > 0:
                percentile[fact].append((val, prc))

        for fact in percentile.keys():
            if fact == 'emotion':
                se.emotion = percentile[fact]
            if fact == 'suitable_stage':
                se.suitable_stage = percentile[fact]
            if fact == 'hedonics':
                se.hedonics = percentile[fact]
            if fact == 'freshness':
                se.freshness = percentile[fact]

        data = elastic.convert_for_bulk(se, 'update')
        bulk_data.append(data)
        count = count + 1
        if count > 100:
            bulk(models.client, actions=bulk_data, stats_only=True)
            total_count = total_count + count
            print("crawl_studies_facts: written another batch, total written {0:d}".format(total_count))
            bulk_data = []
            count = 1

    bulk(models.client, actions=bulk_data, stats_only=True)
    pass

def abstract(map_s, row_s):
    global driver

    if driver == None:
        options = []
        options.append('--load-images=false')
        options.append('--ignore-ssl-errors=true')
        options.append('--ssl-protocol=any')
        #driver = webdriver.PhantomJS(executable_path='C:/Python34/phantomjs.exe', service_args=options)
        #driver = webdriver.PhantomJS(service_args=options)
        driver = webdriver.Chrome()
        driver.set_window_size(1120, 550)
        driver.set_page_load_timeout(3) # seconds
        driver.implicitly_wait(30) # seconds
    publication = row_s['Publication Number']
    url = row_s['url']
    try:
        #print("read_page: scraping url ", url)
        #html = urllib.request.urlopen(url)
        #bs = BeautifulSoup(html.read(), "lxml")
        #[script.decompose() for script in bs("script")]
        print("abstract: scraping publication", publication)
        driver.get(url)
        print("abstract: driver.get", publication)
    except:
        print("abstract: could not open url ", url)
    try:
        #time.sleep(3)
        abstract_tag = driver.find_element_by_id("PAT.ABE")
        print("abstract: driver.find_element_by_id", publication)
        print("abstract: abstract_tag.text", abstract_tag.text)
        tries = 0
        abstract_text = abstract_tag.text
        while len(abstract_text) == 0 and tries < 10000:
            abstract_text = abstract_tag.text
            tries = tries + 1
        print("abstract: abstract_text", abstract_text)
        print("abstract: TRIES", tries)
        #delay = 3 # seconds
        #abstract_tag = WebDriverWait(driver, delay).until(EC.presence_of_element_located((By.ID, "PAT.ABE")))
    except:
        print("abstact: loading took too much time!")
        abstract_text = ""

    return abstract_text
        

    

def crawl_excel(excel_filename, excel_choices):
    global driver

    excel_file = os.path.join(BASE_DIR, 'data/' + excel_filename)
    try:
        mapping_df = pd.read_excel(excel_file, sheetname="mapping", header=0)
    except:
        cwd = os.getcwd()
        print("crawl_excel: working dirtory is: ", cwd)
        print("crawl_excel: excel_file: ", excel_file)
        return False

    mapping_df.fillna("", inplace=True)

    es_host = ES_HOSTS[0]
    headers = {}
    if 'http_auth' in es_host:
        headers['http_auth'] = es_host['http_auth']
    host = es_host['host']
    doc_type = os.path.splitext(excel_filename)[0]
    index = "excel_" + doc_type
    url = "http://" + host + ":9200/" + index

    # The idea is that each excel file ends up in its own mapping (doc_type).
    # (re-)loading a workbook means deleting the existing content and mapping and
    # (re-)create the new mapping with loading the content. However it is not
    # possible anymore to delete a doc_type. For the time being the whole index will
    # be deleted.

    # delete and re-create excel index
    if 'recreate' in excel_choices:
        r = requests.delete(url, headers=headers)
        r = requests.put(url, headers=headers)
        # create mapping in excel index
        properties = {
            'subset' : {'type' : 'string', 'fields' : {'keyword' : {'type' : 'keyword', 'ignore_above' : 256}}}
            }
        for map_key, map_s in mapping_df.iterrows():
            field = map_s['field']
            if field == "":
                continue
            format = map_s['format']
            type = map_s['type']
            if type == 'string':
                properties[field] = {'type' : 'string', 'fields' : {'keyword' : {'type' : 'keyword', 'ignore_above' : 256}}}
            elif type == 'date':
                properties[field] = {'type' : 'date'}
            elif type == 'integer':
                properties[field] = {'type' : 'integer'}
            elif type == 'text':
                properties[field] = {'type' : 'text'}
            elif type == 'list':
                pass
                #properties[field] = {'type' : 'string', 'fields' : {'keyword' : {'type' : 'keyword', 'ignore_above' : 256}}}
                #properties[field] = { 'properties' :
                #                     { field : {'type' : 'string', 'fields' : {'keyword' : {'type' : 'keyword', 'ignore_above' : 256}}}}
                #                    }

        mapping = json.dumps({
                'properties' : properties
            })
        r = requests.put(url + "/_mapping/" + doc_type, headers=headers, data=mapping)
    ## store document
    #data = json.dumps({
    #    "aop" : ["Creative"],
    #    "role" : "Creative Incubators",
    #    "name" : "113Industries, US   Razi Imam",
    #    "link" : "http://113industries.com/",
    #    "why"  : "A scientific research and innovation company made up of scientists and entrepreneurs, who works with leading Fortune 500 companies to help them invent their next generation products based on Social Design-Driven Innovation process and ensure their economic viability by rapidly innovating new products",
    #    "how"  : "Use the power of Big Data to analyze over 200,000 consumer conversations related to product consumption to generate an accurate profile of the consumers, their compensating behaviors and most of all their unarticulated needs.",
    #    "what" : "Social Design-Driven Innovation project to Discover insights, compensating behaviors and unarticulated needs of consumers in relation to air care in the home and auto space in the United States and United Kingdom Open new markets with innovative new products, solutions, and services or business model improvements that will create differentiation to IFF current and potential customers",
    #    "who" : "Razi Imam  razii@113industries.com",
    #    "country" : "USA",
    #    "contacts" : ["Razi Imam"],
    #    "company" : "113 Industries"
    #    })
    #r = requests.put(url + "/" + doc_type + "/1", headers=headers, data=data)
    # query excel
    query = json.dumps({
        "query": {
            "match_all": {}
            }
        })
    r = requests.get(url + "/" + doc_type + "/_search", headers=headers, data=query)
    results = json.loads(r.text)

    data_df = pd.read_excel(excel_file, sheetname="data", header=0)
    data_df.fillna("", inplace=True)
    bulk_data = []
    count = 1
    total_count = 0
    for key, row_s in data_df.iterrows():
        doc = None
        doc = {}
        doc['subset'] = doc_type
        for map_key, map_s in mapping_df.iterrows():
            field = map_s['field']
            if field == "":
                continue
            format = map_s['format']
            column = map_s['column']
            type = map_s['type']
            if format == 'script':
                module = sys.modules[__name__]
                if hasattr(module, field):
                    doc[field] = getattr(module, field)(map_s, row_s)
            else:
                if type == 'list':
                    if field not in doc:
                        doc[field] = []
                    if row_s[column] != "":
                        if len(format) > 0:
                            delimiter = format
                            items = row_s[column].split(delimiter)
                            for item in items:
                                doc[field].append(item)
                        else:
                            doc[field].append(row_s[column])
                else:
                    doc[field] = row_s[column]
        if 'id' in doc:
            id = doc['id']
        else:
            id = str(count)
        data = json.dumps(doc)
        print("crawl_excel: write excel line with id", id)
        r = requests.put(url + "/" + doc_type + "/" + id, headers=headers, data=data)
        print("crawl_excel: written excel line with id", id)
        count = count + 1

    #if driver != None:
    #    driver.quit()
    return True

def crawl_scentemotion(cft_filename):
    ml_file = 'data/' + cft_filename
    cft_df = pd.read_csv(ml_file, sep=';', encoding='ISO-8859-1', low_memory=False)
    cft_df.fillna(0, inplace=True)
    cft_df.index = cft_df['cft_id']
    bulk_data = []
    count = 0
    total_count = 0
    for cft_id, cft_s in cft_df.iterrows():
        se = models.ScentemotionMap()
        se.cft_id = cft_id
        se.dataset = "ingredients"
        se.ingr_name = cft_s.ingr_name
        se.IPC = cft_s.IPC
        se.supplier = cft_s.supplier
        se.olfactive = cft_s.olfactive
        se.region = cft_s.region
        se.review = cft_s.review
        se.dilution = cft_s.dilution
        se.intensity = cft_s.intensity

        percentile = {}
        for col in cft_s.index:
            col_l = col.split("_", 1)
            fct = col_l[0]
            if fct not in ["mood", "smell", "negative", "descriptor", "color", "texture"]:
                continue
            if fct not in percentile.keys():
                percentile[fct] = []
            val = col_l[1]
            prc = cft_s[col]
            if prc > 0:
                #percentile[fct].append((val, "{0:4.2f}".format(prc)))
                percentile[fct].append((val, prc))

        se.mood = percentile["mood"]
        se.smell = percentile["smell"]
        se.negative = percentile["negative"]
        se.descriptor = percentile["descriptor"]
        se.color = percentile["color"]
        se.texture = percentile["texture"]

        data = elastic.convert_for_bulk(se, 'update')
        bulk_data.append(data)
        count = count + 1
        if count > 100:
            bulk(models.client, actions=bulk_data, stats_only=True)
            total_count = total_count + count
            print("crawl_scentemotion: written another batch, total written {0:d}".format(total_count))
            bulk_data = []
            count = 1

    bulk(models.client, actions=bulk_data, stats_only=True)
    pass

def map_survey(survey_filename, map_filename):
    if map_filename != '':
        survey.qa = survey.qa_map(map_filename)
    survey_name = os.path.splitext(survey_filename)[0].split('-', 1)[0].strip()
    ml_file = 'data/' + survey_filename
    survey_df = pd.read_csv(ml_file, sep=';', encoding='ISO-8859-1', low_memory=False)
    survey_df.fillna(0, inplace=True)
    field_map , col_map = survey.map_columns(survey_name, survey_df.columns)
    return col_map


def crawl_survey1(survey_filename, map_filename):
    if map_filename != '':
        survey.qa = survey.qa_map(map_filename)
    survey_name = os.path.splitext(survey_filename)[0].split('-', 1)[0].strip()
    ml_file = 'data/' + survey_filename
    survey_df = pd.read_csv(ml_file, sep=';', encoding='ISO-8859-1', low_memory=False)
    survey_df.fillna(0, inplace=True)
    # col_map[column]: (field, question, answer, dashboard)
    # field_map[field]: [question=0, answer=1, column=2, field_type=3)]
    field_map , col_map = survey.map_columns(survey_name, survey_df.columns)
    survey_df.index = survey_df[field_map['resp_id'][0][2]]
    bulk_data = []
    count = 0
    total_count = 0
    for resp_id, survey_s in survey_df.iterrows():
        resp_id = survey.answer_value_to_string(survey_s[field_map['resp_id'][0][2]])
        blindcode = survey.answer_value_to_string(survey_s[field_map['blindcode'][0][2]])
        #sl = models.SurveyMap()
        #sl.resp_id = resp_id+"_"+blindcode
        #sl.survey  = survey_name
        data = {}
        data['_id'] = resp_id+"_"+blindcode
        data['resp_id'] = resp_id+"_"+blindcode
        data['survey'] = survey_name
        for field, maps in field_map.items():
            # resp_id is the unique id of the record, this is already set above
            if field == 'resp_id':
                continue
            # map: 0=question, 1=answer, 2=column, 3=field_type
            map = maps[0]
            answer_value = survey_s[map[2]]
            answer_value = survey.answer_value_to_string(answer_value)
            answer_value = survey.answer_value_encode(map[0], map[1], field, answer_value)
            # column mapping, no question
            if map[0] == None:
                # in case of multiple mapping search for the column that has a value
                for ix in range(1, len(maps)):
                    map = maps[ix]
                    answer_value_2 = survey_s[map[2]]
                    answer_value_2 = survey.answer_value_to_string(answer_value_2)
                    if (field == 'blindcode'):
                        answer_value = answer_value + '-' + answer_value_2[:3]
                    else:
                        if len(answer_value_2) > len(answer_value):
                            answer_value = answer_value_2
                #setattr(sl, field, answer_value)
                elastic.convert_field(data, field, map, answer_value)
            # question mapping, no answer
            elif map[1][0] == '_':
                #setattr(sl, field, answer_value)
                elastic.convert_field(data, field, map, answer_value)
            # answer mapping
            else:
                #setattr(sl, field, {map[1]: answer_value})
                #attr = getattr(sl, field)
                for ix in range(1, len(maps)):
                    map = maps[ix]
                    answer_value = survey_s[map[2]]
                    answer_value = survey.answer_value_to_string(answer_value)
                    answer_value = survey.answer_value_encode(map[0], map[1], field, answer_value)
                    #attr[map[1]] = answer_value
                    ##attr.append({map[1]: answer_value})
                    elastic.convert_field(data, field, map, answer_value)
        #data = elastic.convert_for_bulk(sl, 'update')
        data = elastic.convert_data_for_bulk(data, 'survey', 'survey', 'update')
        bulk_data.append(data)
        count = count + 1
        if count > 10:
            bulk(models.client, actions=bulk_data, stats_only=True)
            total_count = total_count + count
            print("crawl_survey: written another batch, total written {0:d}".format(total_count))
            bulk_data = []
            count = 1
            #break

    bulk(models.client, actions=bulk_data, stats_only=True)
    pass


def crawl_survey(survey_filename, map_filename):
    survey_name = os.path.splitext(survey_filename)[0].split('-', 1)[0].strip()
    if survey_name == 'fresh and clean':
         crawl_survey1(survey_filename, map_filename)
    elif survey_name == 'orange beverages':
         crawl_survey1(survey_filename, map_filename)
    elif survey_name == 'global panels':
         crawl_survey1(survey_filename, map_filename)
