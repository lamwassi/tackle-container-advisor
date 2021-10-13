# *****************************************************************
# Copyright IBM Corporation 2021
# Licensed under the Eclipse Public License 2.0, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# *****************************************************************

import configparser
import logging
import sqlite3
import os
import json
import urllib.parse as uparse
import multiprocessing
from sqlite3 import Error
from sqlite3.dbapi2 import Cursor, complete_statement
from pathlib import Path
from db import create_db_connection
from sim_applier import sim_applier
import requests
from time import time

def run_entity_linking(data_to_qid, context='software'):
    """
    Runs entity linking on zero shot test set

    :param data_to_qid: Dictionary containing mapping of test mention to Wikidata qid
    :type data_to_qid: <class 'dict'> 
    :param context: Context to be applied to entity linking API
    :type context: string

    :returns: Returns a dictionary of test mention to predicted Wikidata qid
    """
    EL_URL     = config_obj['url']['el_url']
    payload    = []
    id_to_data = {}
    for i, data in enumerate(data_to_qid):
        qid = data_to_qid[data]
        id_to_data[str(i)] = (data, qid)   
        payload.append({'id': i, 'mention': data, 'context_left': '', 'context_right': context})

    el_qids   = {}
    data_json = json.dumps(payload)
    headers   = {'Content-type': 'application/json'}
    try:
        response = requests.post(EL_URL, data=data_json, headers=headers)
        candidates = response.json()
    except Exception as e:
        print("Error querying entity linking url", EL_URL, ":", e)
        return el_qids

    for i in candidates:
        mention = id_to_data[i][0]
        el_qids[mention] = el_qids.get(mention, [])
        cdata = eval(candidates[i]['top_k_entities'])
        for j, candidate in enumerate(cdata):
            qid = candidate[1]
            el_qids[mention].append(qid)

    return el_qids

def get_data_combinations(data):
    """
    Generate phrases from words in data

    :param data: A list of mention words e.g. ['Apache', 'Tomcat', 'HTTP', 'Server']
    :type data: list 

    # :returns: Returns a list of truncated phrases e.g ['Tomcat HTTP Server', 'HTTP Server', ... 'Apache Tomcat HTTP', 'Apache Tomcat', ...]
    :returns: Returns a list of truncated phrases e.g ['Apache Tomcat HTTP', 'Apache Tomcat', ... , 'Tomcat HTTP Server', 'HTTP Server', ...]

    """
    combinations = []
    combinations.append(' '.join(data))
    for i in range(1,len(data),1):
        combinations.append(' '.join(data[:-i]))
    for i in range(1,len(data),1):
        combinations.append(' '.join(data[i:]))
    return combinations


def invoke_wikidata_api(data):
    """
    Invokes wikidata autocomplete on data

    :param data: String to query Wikidata API for qids
    :type data: string 

    :returns: Returns a list of qids
    """
    qids    = []
    headers = {'Content-type': 'application/json'}    
    WD_URL  = config_obj['url']['wd_url']
    try:
        response = requests.get(WD_URL+uparse.quote(data), headers=headers)
        candidates = response.json()
        if candidates['success'] != 1:
            logging.error(f"Failed wikidata query -> {candidates}")
        else:
            for candidate in candidates['search']:
                qids.append(candidate['id'])
    except Exception as e:
        logging.error(f"Error querying wikidata url {WD_URL} : {e}")

    return qids

def get_wikidata_qids(data):
    """
    Gets wikidata qids for data

    :param data: Mention for which to get Wikidata qids
    :type data: string 

    :returns: Returns a dictionary of data to predicted qids
    """
    wd_qids = {}

    # Get qids for exact phrase
    qids  = []
    qids += invoke_wikidata_api(data)    
    # print("Len of qids after exact = ", len(qids))

    fragments    = data.split(' ')
    # Get valid fragments
    data_to_qids = {}
    for i, frag in enumerate(fragments):
        frqids = invoke_wikidata_api(frag)            
        if frqids:
            data_to_qids[frag] = frqids           

    # Get qids for combinations of all fragments
    combinations = get_data_combinations(fragments)
    for i, comb in enumerate(combinations):
        qids += invoke_wikidata_api(comb)        
    # print("Len of qids after all combos = ", len(qids))

    # Get qids for combinations of sorted valid fragments
    data_to_qids = {k: v for k, v in sorted(data_to_qids.items(), key=lambda item: len(item[1]))}
    valid_data   = [d for d in data_to_qids]
    combinations = get_data_combinations(valid_data)
    for i, comb in enumerate(combinations):
        qids += invoke_wikidata_api(comb)        
    # print("Len of qids after sorted fragment combos = ", len(qids))

    if not qids:           
        logging.info(f"No qids for {data}")                    
    
    wd_qids[data] = qids
    return wd_qids

def run_wikidata_autocomplete(data_to_qid):
    """
    Runs wikidata autocomplete on zero shot test set

    :param data_to_qid: Dictionary containing mapping of test mention to Wikidata qid
    :type data_to_qid: <class 'dict'> 

    :returns: Returns a dictionary of test mention to predicted Wikidata qid
    """
    pool             = multiprocessing.Pool(2*os.cpu_count())
    wd_results       = pool.map(get_wikidata_qids, data_to_qid.keys())
    pool.close()

    wd_qids = {k:v for item in wd_results for k,v in item.items()}
    return wd_qids

def get_topk_accuracy(data_to_qid, alg_qids):
    """
    Print top-1, top-3, top-5, top-10, top-inf accuracy 

    :param data_to_qid: Dictionary containing mapping of test mention to correct Wikidata qid
    :type data_to_qid: <class 'dict'>
    :param alg_qids: Dictionary containing mapping of test mention to list of predicted Wikidata qids    
    :type alg_qids: <class 'dict'>

    :returns: Prints top-1, top-3, top-5, top-10, top-inf accuracy
    """

    total_mentions = len(data_to_qid)
    topk  = (0, 0, 0, 0, 0) # Top-1, top-3, top-5, top-10, top-inf
    for mention in data_to_qid:
        correct_qid = data_to_qid[mention]
        qids = alg_qids.get(mention, None)
        if not qids:
            continue
        for i, qid in enumerate(qids):
            if qid == correct_qid: 
                topk = (topk[0],topk[1],topk[2],topk[3],topk[4]+1)
                if i <= 0:
                    topk = (topk[0]+1,topk[1],topk[2],topk[3],topk[4]) 
                if i <= 2:
                    topk = (topk[0],topk[1]+1,topk[2],topk[3],topk[4])
                if i <= 4:
                    topk = (topk[0],topk[1],topk[2]+1,topk[3],topk[4])
                if i <= 9:
                    topk = (topk[0],topk[1],topk[2],topk[3]+1,topk[4])
                break

    print(f"Top-1 = {topk[0]/total_mentions:.2f}, top-3 = {topk[1]/total_mentions:.2f}, top-5 = {topk[2]/total_mentions:.2f}, top-10= {topk[3]/total_mentions:.2f}, top-inf = {topk[4]/total_mentions:.2f}({topk[4]})")

def run_zero_shot():
    zs_test_filename = os.path.join(config_obj['benchmark']['data_path'], 'zs_test.csv')        

    if not os.path.isfile(zs_test_filename):
        logging.error(f'{zs_test_filename} is not a file. Run "python benchmarks.py" to generate this test data file')
        print(f'{zs_test_filename} is not a file. Run "python benchmarks.py" to generate this test data file')
        exit()
    else:
        data_to_qid = {}
        try:
            zs_test_filename = os.path.join(config_obj['benchmark']['data_path'], 'zs_test.csv')        
            with open(zs_test_filename, 'r') as zero_shot:            
                test = [d.strip() for d in zero_shot.readlines()]
                for row in test:
                    (data, qid) = tuple(row.split('\t'))
                    data_to_qid[data] = qid
        except OSError as exception:
            logging.error(exception)
            exit()
        
        print("---------------------------------------------")
        print("Testing zero shot algorithms on %d mentions." % len(data_to_qid))
        print("---------------------------------------------")
      
        el_start= time()
        el_qids = run_entity_linking(data_to_qid, context='')
        el_end  = time()
        print(f'EL with no ctx took {(el_end-el_start):.2f} seconds: ', end='')
        get_topk_accuracy(data_to_qid, el_qids)
      
        wd_start= time()
        wd_qids = run_wikidata_autocomplete(data_to_qid)
        wd_end  = time()
        print(f'WD api with no ctx took {(wd_end-wd_start):.2f} seconds: ', end='')
        get_topk_accuracy(data_to_qid, wd_qids)
        
        elctx_start= time()    
        elctx_qids = run_entity_linking(data_to_qid)
        elctx_end  = time()    
        print(f'EL with ctx=software took {(elctx_end-elctx_start):.2f} seconds: ', end='')
        get_topk_accuracy(data_to_qid, elctx_qids)

        for data, qid in data_to_qid.items():    
            if qid in elctx_qids[data] and qid not in wd_qids[data]:
                logging.info(f"Data = {data}, QID = {qid} not in {wd_qids[data]}")
        

def run_few_shot(connection):
    fs_test_filename = os.path.join(config_obj['benchmark']['data_path'], 'fs_test.csv')
    
    if not os.path.isfile(fs_test_filename):
        logging.error(f'{fs_test_filename} is not a file. Run "python benchmarks.py" to generate this test data file')
        print(f'{fs_test_filename} is not a file. Run "python benchmarks.py" to generate this test data file')
        exit()
    else:
        entity_cursor = connection.cursor()

    entity_cursor.execute("SELECT * FROM entities")
    entity_to_eid  = {}
    for entity_tuple in entity_cursor.fetchall():
        entity_id, entity, entity_type_id, external_link = entity_tuple
        entity_to_eid[entity] = entity_id

    data_to_eid = {}
    try:
        with open(fs_test_filename, 'r') as few_shot:            
            test = [d.strip() for d in few_shot.readlines()]
            for row in test:
                (data, eid) = tuple(row.split('\t'))
                data_to_eid[data] = eid
    except OSError as exception:
        logging.error(exception)
        exit()

    mentions  = data_to_eid.keys()
    test_data = ",".join(mentions)

    model_path = config_obj["model"]["model_path"]         
    sim_app    = sim_applier(model_path)
    start      = time()
    tech_sim_scores=sim_app.tech_stack_standardization(test_data)    
    end        = time()
    num_correct=0
    for mention, entity in zip(mentions, tech_sim_scores):
        correct_eid   = data_to_eid.get(mention, None)     
        predicted_eid = entity_to_eid.get(entity[0], None)
        if not correct_eid:
            logging.error(f"Mention {mention} not found in data_to_eid.")
        if correct_eid != predicted_eid:
            num_correct += 1
    print(f"---------------------------------------------")
    print(f"Testing few shot algorithms with {len(mentions)} mentions.")
    print(f"---------------------------------------------")
    print(f"tf-idf took {(end-start):.2f} seconds: accuracy = {(num_correct/len(mentions)):.2f}")

config_obj = configparser.ConfigParser()
config_obj.read("./config.ini")

logging.basicConfig(filename='logging.log',level=logging.DEBUG, \
                    format="[%(levelname)s:%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s", filemode='w')

if __name__ == '__main__':
    try:
        db_path = config_obj["db"]["db_path"]
    except KeyError as k:
        logging.error(f'{k}  is not a key in your config.ini file.')
        print(f'{k} is not a key in your config.ini file.')
        exit()

    try:
        data_path = config_obj["benchmark"]["data_path"]
    except KeyError as k:
        logging.error(f'{k}  is not a key in your config.ini file.')
        print(f'{k} is not a key in your config.ini file.')
        exit()    

    if not os.path.isfile(db_path):
        logging.error(f'{db_path} is not a file. Run "sh setup" from /tackle-container-advisor folder to generate db files')
        print(f'{db_path} is not a file. Run "sh setup.sh" from /tackle-container-advisor folder to generate db files')
        exit()
    else:
        connection = create_db_connection(db_path)
        run_few_shot(connection)
        run_zero_shot()
