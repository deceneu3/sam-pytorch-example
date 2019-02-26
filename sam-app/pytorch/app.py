try:
    import unzip_requirements
except ImportError:
    pass

import os
import io
import json
import tarfile
import glob
import time
import logging

import boto3
import requests

# import torch after package dependencies
import torch
import torch.nn.functional as F

import PIL
from torchvision import models, transforms

s3 = boto3.client('s3')

classes = []

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MODEL_BUCKET=os.environ.get('MODEL_BUCKET')
logger.info(f'Model Bucket is {MODEL_BUCKET}')

MODEL_PREFIX=os.environ.get('MODEL_PREFIX')
logger.info(f'Model Prefix is {MODEL_PREFIX}')

# processing pipeline
preprocess = transforms.Compose([
    transforms.Resize(256),
    transforms.CenterCrop(224),
    transforms.ToTensor(),
    transforms.Normalize(
        mean=[0.485, 0.456, 0.406],
        std=[0.229, 0.224, 0.225]
    )
])

# loads the PyTorch model from S3
def load_model():
    global classes
    logger.info('Loading model from S3')
    model_dir = '/tmp/model'
    local_model=f'{model_dir}/model.tar.gz'
    # download the file from S3 and extract
    logger.info(f'Downloading model from S3 to {local_model}')
    if not os.path.exists(model_dir):
        os.mkdir(model_dir)
    s3.download_file(
                    MODEL_BUCKET, f'{MODEL_PREFIX}/model.tar.gz', local_model)
    logger.info('Opening model tarfile')
    tarfile.open(local_model).extractall(model_dir)
    os.remove(local_model)
    logger.info('Getting classes from file')
    # get the classes from saved 'classes.txt' file
    with open(f'{model_dir}/classes.txt', 'r') as f:
        classes = f.read().splitlines()
    logger.info(f'Classes are {classes}')    
    model_path = glob.glob(f'{model_dir}/*_jit.pth')[0]
    logger.info(f'Model path is {model_path}')
    model = torch.jit.load(model_path, map_location=torch.device('cpu'))
    return model.eval()

# load the model   
model = load_model()

# the method which passes the object through the model
def predict(input_object, model):
    logger.info("Calling prediction on model")
    start_time = time.time()
    predict_values = model(input_object)
    logger.info("--- Inference time: %s seconds ---" % (time.time() - start_time))
    preds = F.softmax(predict_values, dim=1)
    conf_score, indx = torch.max(preds, dim=1)
    predict_class = classes[indx]
    logger.info(f'Predicted class is {predict_class}')
    logger.info(f'Softmax confidence score is {conf_score.item()}')
    response = {}
    response['class'] = str(predict_class)
    response['confidence'] = conf_score.item()
    return response
    
# the method that takes the URL from the request body, downloads the image and creates a Tensor object
def input_fn(request_body):
    logger.info("Getting input URL to a image Tensor object")
    if isinstance(request_body, str):
        request_body = json.loads(request_body)
    img_request = requests.get(request_body['url'], stream=True)
    img = PIL.Image.open(io.BytesIO(img_request.content))
    img_tensor = preprocess(img)
    img_tensor = img_tensor.unsqueeze(0)
    return img_tensor
    
def lambda_handler(event, context):
    """Sample pure Lambda function

    Parameters
    ----------
    event: dict, required
        API Gateway Lambda Proxy Input Format

        Event doc: https://docs.aws.amazon.com/apigateway/latest/developerguide/set-up-lambda-proxy-integrations.html#api-gateway-simple-proxy-for-lambda-input-format

    context: object, required
        Lambda Context runtime methods and attributes

        Context doc: https://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html

    Returns
    ------
    API Gateway Lambda Proxy Output Format: dict

        Return doc: https://docs.aws.amazon.com/apigateway/latest/developerguide/set-up-lambda-proxy-integrations.html
    """
    print("Starting event")
    logger.info(event)
    print("Getting input object")
    input_object = input_fn(event['body'])
    print("Calling prediction")
    response = predict(input_object, model)
    print("Returning response")
    return {
        "statusCode": 200,
        "body": json.dumps(response)
    }