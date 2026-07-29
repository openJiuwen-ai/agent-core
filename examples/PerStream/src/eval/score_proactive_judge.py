# Based on https://github.com/haotian-liu/LLaVA.

import os
import ast
import json
import openai
import argparse
from tqdm import tqdm
from time import sleep
from collections import defaultdict
from multiprocessing.pool import Pool

def parse_args():
    parser = argparse.ArgumentParser(description="question-answer-generation-using-gpt-3")
    parser.add_argument("--pred_path", required=True, help="The path to file containing prediction.")
    parser.add_argument("--output_dir", required=True, help="The path to save annotation json files.")
    parser.add_argument("--output_json", required=True, help="The path to save annotation final combined json file.")
    parser.add_argument("--num_tasks", required=True, type=int, help="Number of splits.")
    parser.add_argument("--num_chunks", default=1, type=int, help="Result splits")
    parser.add_argument("--api_key", required=True, type=str, help="OpenAI API key")
    parser.add_argument("--api_type", default=None, type=str, help="OpenAI API type")
    parser.add_argument("--api_version", default=None, type=str, help="OpenAI API version")
    parser.add_argument("--api_base", default=None, type=str, help="OpenAI API base")
    args = parser.parse_args()
    return args


def is_nil(text):
    """Check if a text is considered nil."""
    if text is None:
        return True
    return text.strip().lower() == "nil" or text.strip().lower() == "[silent]" or text.strip().lower() == "" or text.strip().lower() == "no" or text.strip().lower() == "</silence>"


def annotate(prediction_set, caption_files, output_dir, api_key):
    """
    Evaluates question and answer pairs using GPT-3
    Returns a score for correctness.
    """
    for file in tqdm(caption_files):
        key = file[:-5] # Strip file extension
        qa_set = prediction_set[key]
        question = qa_set['q']
        answer = qa_set['a']
        pred = qa_set['pred']
        
        # Check if pred or answer is nil
        pred_is_nil = is_nil(pred)
        answer_is_nil = is_nil(answer)
        
        # If both are nil or both are not nil, we need LLM evaluation
        if pred_is_nil and answer_is_nil:
            # Both nil - perfect match
            result_qa_pair = [{'pred': 'NA', 'score': 0}, qa_set]
            with open(f"{output_dir}/{key}.json", "w") as f:
                json.dump(result_qa_pair, f)
            continue
        elif pred_is_nil or answer_is_nil:
            # One is nil, one is not - mismatch, no need for LLM
            result_qa_pair = [{'pred': 'NA', 'score': 0}, qa_set]
            with open(f"{output_dir}/{key}.json", "w") as f:
                json.dump(result_qa_pair, f)
            continue
        
        # Both are not nil - use LLM to evaluate
        try:
            # Try OpenAI v1.0+ format first
            try:
                from openai import OpenAI
                # Initialize the client with API key from environment variable or pass it directly
                client = OpenAI(api_key=api_key)  # Initialize the client with API key
                
                completion = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {
                            "role": "system",
                            "content": 
                                "You are an intelligent chatbot designed for evaluating the correctness of generative outputs for question-answer pairs. "
                                "Your task is to compare the predicted answer with the correct answer and determine if they match meaningfully. Here's how you can accomplish the task:"
                                "------"
                                "##INSTRUCTIONS: "
                                "- Focus on the meaningful match between the predicted answer and the correct answer.\n"
                                "- Consider synonyms or paraphrases as valid matches.\n"
                                "- Evaluate the correctness of the prediction compared to the answer."
                        },
                        {
                            "role": "user",
                            "content":
                                "Please evaluate the following video-based question-answer pair:\n\n"
                                f"Question: {question}\n"
                                f"Correct Answer: {answer}\n"
                                f"Predicted Answer: {pred}\n\n"
                                "Provide your evaluation only as a yes/no and score where the score is an integer value between 0 and 5, with 5 indicating the highest meaningful match. "
                                "Please generate the response in the form of a Python dictionary string with keys 'pred' and 'score', where value of 'pred' is  a string of 'yes' or 'no' and value of 'score' is in INTEGER, not STRING."
                                "DO NOT PROVIDE ANY OTHER OUTPUT TEXT OR EXPLANATION. Only provide the Python dictionary string. "
                                "For example, your response should look like this: {'pred': 'yes', 'score': 4.8}."
                        }
                    ],
                    temperature=0.002
                )
                # Convert response to a Python dictionary (v1.0+ format)
                response_message = completion.choices[0].message.content
                
            except (ImportError, AttributeError):
                print("Fallback to legacy")
                # Fallback to legacy format
                completion = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {
                            "role": "system",
                            "content": 
                                "You are an intelligent chatbot designed for evaluating the usefulness and correctness of generative outputs for proactive response. "
                                "Your task is to compare the predicted answer with the correct answer and determine if they have same intention (be more lenient on proactive). Here's how you can accomplish the task:"
                                "------"
                                "##INSTRUCTIONS: "
                                "- Focus on the meaningful match between the predicted answer and the correct answer.\n"
                                "- Consider synonyms or paraphrases as valid matches.\n"
                                "- Evaluate the correctness of the prediction compared to the answer."
                        },
                        {
                            "role": "user",
                            "content":
                                "Please evaluate the following video-based proactive scenario:\n\n"
                                f"Question: {question}\n"
                                f"Correct Answer: {answer}\n"
                                f"Predicted Answer: {pred}\n\n"
                                "Provide your evaluation only as a yes/no and score where the score is an integer value between 0 and 5, with 5 indicating the highest meaningful match. "
                                "Please generate the response in the form of a Python dictionary string with keys 'pred' and 'score', where value of 'pred' is  a string of 'yes' or 'no' and value of 'score' is in INTEGER, not STRING."
                                "DO NOT PROVIDE ANY OTHER OUTPUT TEXT OR EXPLANATION. Only provide the Python dictionary string. "
                                "For example, your response should look like this: {'pred': 'yes', 'score': 4.8}."
                        }
                    ],
                    temperature=0.002
                )
                # Convert response to a Python dictionary (legacy format)
                response_message = completion["choices"][0]["message"]["content"]
            
            print("Response Message:", response_message)
            response_dict = ast.literal_eval(response_message)
            result_qa_pair = [response_dict, qa_set]

            # Save the question-answer pairs to a json file.
            with open(f"{output_dir}/{key}.json", "w") as f:
                json.dump(result_qa_pair, f)
            sleep(0.5)

        except Exception as e:
            print(f"Error processing file '{key}': {e}")
            sleep(1)


def main():
    """
    Main function to control the flow of the program.
    """
    # Parse arguments.
    args = parse_args()
    pred_contents = [json.loads(line) for line in open(args.pred_path)]

    # Dictionary to store the count of occurrences for each video_id
    video_id_counts = {}
    new_pred_contents = []

    # Iterate through each sample in pred_contents
    for sample in pred_contents:
        video_id = sample['id']
        if video_id in video_id_counts:
            video_id_counts[video_id] += 1
        else:
            video_id_counts[video_id] = 0

        # Create a new sample with the modified key
        new_sample = sample
        new_sample['id'] = f"{video_id}_{video_id_counts[video_id]}"
        new_pred_contents.append(new_sample)

    # Generating list of id's and corresponding files
    id_list = [x['id'] for x in new_pred_contents]
    caption_files = [f"{id}.json".replace("/",".") for id in id_list]

    print("len id_list:", len(id_list))
    print("caption files:", caption_files)

    output_dir = args.output_dir
    # Generate output directory if not exists.
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Preparing dictionary of question-answer sets
    prediction_set = {}
    for sample in new_pred_contents:
        id = sample['id']
        question = sample['question']
        answer = sample['answer']
        pred = sample['pred']
        qa_set = {"q": question, "a": answer, "pred": pred, "a_type": sample['answer_type'] if 'answer_type' in sample else None}
        
        # Add latency and vram to qa_set if available
        if 'latency' in sample:
            qa_set['latency'] = sample['latency']
        if 'vram' in sample:
            qa_set['vram'] = sample['vram']
            
        prediction_set[id.replace("/",".")] = qa_set

    # Set the OpenAI API key.
    openai.api_key = args.api_key # Your API key here
    if args.api_type:
        openai.api_type = args.api_type
    if args.api_version:
        openai.api_version = args.api_version
    if args.api_base:
        openai.api_base = args.api_base # Your API base here
    num_tasks = args.num_tasks

    # While loop to ensure that all captions are processed.
    incomplete_lengths = []
    for _ in range(100):
        try:
            # Files that have not been processed yet.
            completed_files = os.listdir(output_dir)
            print(f"completed_files: {len(completed_files)}")

            # Files that have not been processed yet.
            incomplete_files = [f for f in caption_files if f not in completed_files]
            print(f"incomplete_files: {len(incomplete_files)}")
            incomplete_lengths.append(len(incomplete_files))
            if len(incomplete_lengths) > 5 and len(set(incomplete_lengths[-5:])) <= 1:
                print(f"incomplete_lengths: {incomplete_lengths}")
                print(f"incomplete_files: {incomplete_files}")
                print(f"completed_files: {completed_files}")
                print(f"failed for 5 times, break")
                break

            # Break the loop when there are no incomplete files
            if len(incomplete_files) == 0:
                break
            if len(incomplete_files) <= num_tasks:
                num_tasks = 1

            # Split tasks into parts.
            part_len = len(incomplete_files) // num_tasks
            all_parts = [incomplete_files[i:i + part_len] for i in range(0, len(incomplete_files), part_len)]
            task_args = [(prediction_set, part, args.output_dir, args.api_key) for part in all_parts]

            # Use a pool of workers to process the files in parallel.
            with Pool() as pool:
                pool.starmap(annotate, task_args)

        except Exception as e:
            print(f"Error: {e}")

    # Combine all the processed files into one
    combined_contents = {}
    json_path = args.output_json

    # Iterate through json files
    for file_name in os.listdir(output_dir):
        if file_name.endswith(".json"):
            file_path = os.path.join(output_dir, file_name)
            with open(file_path, "r") as json_file:
                content = json.load(json_file)
                assert 'pred' in content[0], f"Error: {file_name} don't has key=pred"
                assert 'score' in content[0], f"Error: {file_name} don't has key=score"
                combined_contents[file_name[:-5]] = content

    # Write combined content to a json file
    with open(json_path, "w") as json_file:
        json.dump(combined_contents, json_file)
    print("All evaluation completed!")

    class ScoreMeter:
        def __init__(self):
            self.score_sum = 0
            self.count = 0
            self.yes_count = 0
            self.no_count = 0
            self.score_dict = {'yes': defaultdict(int), 'no': defaultdict(int)}
            # Add latency and vram tracking
            self.latency_sum = 0
            self.vram_sum = 0
            self.latency_count = 0
            self.vram_count = 0
            
            # Proactive evaluation metrics
            self.tp = 0  # True Positive: GT not nil, Pred not nil, LLM says yes
            self.fp = 0  # False Positive: GT nil, Pred not nil
            self.tn = 0  # True Negative: GT nil, Pred nil
            self.fn = 0  # False Negative: GT not nil, Pred nil
            self.total_gt_not_nil = 0  # For TA (recall)
            self.total_pred_not_nil = 0  # For TV (precision)

        def add_score(self, score, pred, gt_answer, pred_answer, latency=None, vram=None):
            pred_lower = pred.lower()

            if pred != "NA":
                self.score_sum += score
                self.count += 1
            
            gt_is_nil = is_nil(gt_answer)
            pred_is_nil = is_nil(pred_answer)
            
            # Count for TA and TV denominators
            if not gt_is_nil:
                self.total_gt_not_nil += 1
            if not pred_is_nil:
                self.total_pred_not_nil += 1
            
            # Categorize for confusion matrix
            if not gt_is_nil and not pred_is_nil:
                self.tp += 1
                # Both not nil - check LLM evaluation
                if 'yes' in pred_lower:
                    self.yes_count += 1
                    if score > 0:
                        self.score_dict['yes'][score] += 1
                else:
                    # LLM says no, but both provided answers
                    # This is still a "prediction made" but wrong
                    self.no_count += 1
                    if score < 4:
                        self.score_dict['no'][score] += 1
            elif gt_is_nil and pred_is_nil:
                # True Negative
                self.tn += 1
            elif gt_is_nil and not pred_is_nil:
                # False Positive
                self.fp += 1
            elif not gt_is_nil and pred_is_nil:
                # False Negative
                self.fn += 1
                
            # Add latency and vram if provided
            if latency is not None:
                self.latency_sum += latency
                self.latency_count += 1
            if vram is not None:
                self.vram_sum += vram
                self.vram_count += 1

        def get_average_score(self):
            res = (self.score_sum / self.count) if self.count else 0
            return f"{res:.6f}"

        def get_accuracy(self):
            """Accuracy: Only for cases where both GT and Pred are not nil"""
            total_both_not_nil = self.tp + (self.yes_count - self.tp) + self.no_count
            if total_both_not_nil == 0:
                return "0.000000"
            res = self.yes_count / total_both_not_nil
            return f"{res:.6f}"
        
        def get_ta_recall(self):
            """TA (Recall): TP / (TP + FN) = TP / total_gt_not_nil"""
            if self.total_gt_not_nil == 0:
                return "0.000000"
            res = self.tp / self.total_gt_not_nil
            return f"{res:.6f}"
        
        def get_tv_precision(self):
            """TV (Precision): TP / (TP + FP) = TP / total_pred_not_nil"""
            if self.total_pred_not_nil == 0:
                return "0.000000"
            res = self.tp / self.total_pred_not_nil
            return f"{res:.6f}"
        
        def get_f1(self):
            """F1 Score: 2 * (Precision * Recall) / (Precision + Recall)"""
            ta = float(self.get_ta_recall())
            tv = float(self.get_tv_precision())
            if ta + tv == 0:
                return "0.000000"
            f1 = 2 * (tv * ta) / (tv + ta)
            return f"{f1:.6f}"
        
        def get_miss_rate(self):
            """Miss Rate: FN / (TP + FN) = FN / total_gt_not_nil"""
            if self.total_gt_not_nil == 0:
                return "0.000000"
            res = self.fn / self.total_gt_not_nil
            return f"{res:.6f}"
            
        def get_average_latency(self):
            res = (self.latency_sum / self.latency_count) if self.latency_count else 0
            return f"{res:.6f}"
            
        def get_average_vram(self):
            res = (self.vram_sum / self.vram_count) if self.vram_count else 0
            return f"{res:.6f}"

    meter_dic = {'total': ScoreMeter()}
    for key, result in combined_contents.items():
        # Computing score
        score_match = result[0]['score']
        score = int(score_match)
        pred = result[0]['pred']
        
        # Get GT answer and predicted answer
        gt_answer = result[1]['a']
        pred_answer = result[1]['pred']
        
        # Extract latency and vram if available
        latency = result[1].get('latency')
        vram = result[1].get('vram')

        meter_dic["total"].add_score(score, pred, gt_answer, pred_answer, latency, vram)
        if 'a_type' in result[1] and result[1]['a_type'] is not None:
            typ = str(result[1]['a_type'])
            if typ not in meter_dic:
                meter_dic[typ] = ScoreMeter()
            meter_dic[typ].add_score(score, pred, gt_answer, pred_answer, latency, vram)

            if 'next' in args.output_dir:
                typ = typ[0]
                if typ not in meter_dic:
                    meter_dic[typ] = ScoreMeter()
                meter_dic[typ].add_score(score, pred, gt_answer, pred_answer, latency, vram)

    csv_dic = {
        'acc': meter_dic["total"].get_accuracy(),
        'score': meter_dic["total"].get_average_score(),
        'ta_recall': meter_dic["total"].get_ta_recall(),
        'tv_precision': meter_dic["total"].get_tv_precision(),
        'f1': meter_dic["total"].get_f1(),
        'miss_rate': meter_dic["total"].get_miss_rate()
    }
    
    # Add latency and vram to csv_dic
    csv_dic['avg_latency'] = meter_dic["total"].get_average_latency()
    csv_dic['avg_vram'] = meter_dic["total"].get_average_vram()

    output = ""
    output += "=== Confusion Matrix ===\n"
    output += f"TP (GT not nil, Pred not nil, LLM yes): {meter_dic['total'].tp}\n"
    output += f"FP (GT nil, Pred not nil): {meter_dic['total'].fp}\n"
    output += f"TN (GT nil, Pred nil): {meter_dic['total'].tn}\n"
    output += f"FN (GT not nil, Pred nil): {meter_dic['total'].fn}\n"
    output += "\n"
    output += "=== Metrics ===\n"
    output += "Accuracy (both not nil, LLM judges): " + str(meter_dic["total"].get_accuracy()) + "\n"
    output += "Average Score: " + str(meter_dic["total"].get_average_score()) + "\n"
    output += "TA (Recall): " + str(meter_dic["total"].get_ta_recall()) + "\n"
    output += "TV (Precision): " + str(meter_dic["total"].get_tv_precision()) + "\n"
    output += "F1 Score: " + str(meter_dic["total"].get_f1()) + "\n"
    output += "Miss Rate: " + str(meter_dic["total"].get_miss_rate()) + "\n"
    output += "Average latency: " + str(meter_dic["total"].get_average_latency()) + " seconds\n"
    output += "Average VRAM: " + str(meter_dic["total"].get_average_vram()) + " GB\n"
    output += "\n"
    output += "Total Score Yes/No distribution:\n"
    for key, value in meter_dic["total"].score_dict.items():
        output += f"{key}:\n"
        for k in range(0, 6):
            v = value[k]
            output += f"{k}: {v}\n"
    output += "\n"
    output += "Answer Type Score distribution:\n"
    output += 'Type, Accuracy, Avg_score, TA_Recall, TV_Precision, F1, Miss_Rate, Avg_latency, Avg_vram\n'
    key_list = sorted([k for k in meter_dic.keys()])
    for key in key_list:
        output += f"{key}, {meter_dic[key].get_accuracy()}, {meter_dic[key].get_average_score()}, {meter_dic[key].get_ta_recall()}, {meter_dic[key].get_tv_precision()}, {meter_dic[key].get_f1()}, {meter_dic[key].get_miss_rate()}, {meter_dic[key].get_average_latency()}, {meter_dic[key].get_average_vram()}\n"

    output += "\n"
    output += "=== CSV Format ===\n"
    for k in csv_dic.keys():
        output += f"{k}, "
    output = output.rstrip(', ')  # Remove the trailing comma and space
    output += "\n"

    for k in csv_dic.keys():
        output += str(csv_dic[k]) + ", "
    output = output.rstrip(', ')  # Remove the trailing comma and space
    output += "\n"

    print(output)
    args.output_csv = args.output_json.replace(".json", ".csv")
    with open(args.output_csv, 'w') as f:
        f.write(output)

if __name__ == "__main__":
    main()