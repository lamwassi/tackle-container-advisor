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


import re
import logging

class Utils:

    @staticmethod
    def preprocess_tech_stack_for_sim(tech_stack):
        '''
         preprocess_tech_stack_for_sim method takes the input string and removes any non aschii character
         present in that string.
        '''

        try:
            tech_stack = re.sub(r'[^\x00-\x7F]+', " ", " " + str(tech_stack) + " ").strip()
            return tech_stack

        except Exception as e:
            logging.error(str(e))

    
    @staticmethod
    def mergeDicts(result, app_tech):
        """
        mergeDicts merges the nested dictionaries
        """
        try:
            if not result:
                result = {}
            if not app_tech:
                return result
            for snippet, obj in app_tech.items():
                if not obj:
                    continue
                for tech, version in obj.items():
                    if snippet not in result:
                        result[snippet] = {}
                    result[snippet][tech] = version
            return result

        except Exception as e:
            logging.error(str(e))
        

