import asyncio
from aiohttp import web, WSCloseCode
import logging
import weakref
import cv2
import time
import PIL.Image
import matplotlib.pyplot as plt
from typing import List
from nanoowl.tree import Tree
from nanoowl.tree_predictor import (
    TreePredictor
)
from nanoowl.tree_drawing import draw_tree_detections
from nanoowl.owl_predictor import OwlPredictor

CAMERA_DEVICE = 0
IMAGE_QUALITY = 50

predictor = TreePredictor(
    owl_predictor=OwlPredictor(
        image_encoder_engine="../../data/owl_image_encoder.engine"
    )
)

prompt_data = None

def get_colors(count: int):
    cmap = plt.cm.get_cmap("rainbow", count)
    colors = []
    for i in range(count):
        color = cmap(i)
        color = [int(255 * value) for value in color]
        colors.append(tuple(color))
    return colors


def cv2_to_pil(image):
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return PIL.Image.fromarray(image)


async def handle_index_get(request: web.Request):
    logging.info("handle_index_get")
    return web.FileResponse("./index.html")


async def websocket_handler(request):

    global prompt_data

    ws = web.WebSocketResponse()

    await ws.prepare(request)

    logging.info("Websocket connected.")

    request.app['websockets'].add(ws)

    try:
        async for msg in ws:
            logging.info(f"Received message from websocket.")
            if "prompt" in msg.data:
                header, prompt = msg.data.split(":")
                logging.info("Received prompt: " + prompt)
                try:
                    tree = Tree.from_prompt(prompt)
                    clip_encodings = predictor.encode_clip_labels(tree)
                    owl_encodings = predictor.encode_owl_labels(tree)
                    prompt_data = {
                        "tree": tree,
                        "clip_encodings": clip_encodings,
                        "owl_encodings": owl_encodings
                    }
                    logging.info("Set prompt: " + prompt)
                except Exception as e:
                    print(e)
    finally:
        request.app['websockets'].discard(ws)

    return ws


async def on_shutdown(app: web.Application):
    for ws in set(app['websockets']):
        await ws.close(code=WSCloseCode.GOING_AWAY,
                       message='Server shutdown')


async def detection_loop(app: web.Application):

    loop = asyncio.get_running_loop()

    logging.info("Opening camera.")

    camera = cv2.VideoCapture(CAMERA_DEVICE)

    logging.info("Loading predictor.")

    def _read_and_encode_image():

        re, image = camera.read()

        if not re:
            return re, None

        image_pil = cv2_to_pil(image)

        if prompt_data is not None:
            prompt_data_local = prompt_data
            detections = predictor.predict(
                image_pil,
                tree=prompt_data_local['tree'],
                clip_text_encodings=prompt_data_local['clip_encodings'],
                owl_text_encodings=prompt_data_local['owl_encodings']
            )
            tree = prompt_data_local['tree']
            print(tree.labels)
            image = draw_tree_detections(image, detections, prompt_data['tree'])

        image_jpeg = bytes(
            cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, IMAGE_QUALITY])[1]
        )

        return re, image_jpeg

    while True:

        re, image = await loop.run_in_executor(None, _read_and_encode_image)
        
        if not re:
            break
        
        for ws in app["websockets"]:
            await ws.send_bytes(image)

    camera.release()


async def run_detection_loop(app):
    try:
        task = asyncio.create_task(detection_loop(app))
        yield
        task.cancel()
    except asyncio.CancelledError:
        pass
    finally:
        await task


logging.basicConfig(level=logging.INFO)
app = web.Application()
app['websockets'] = weakref.WeakSet()
app.router.add_get("/", handle_index_get)
app.router.add_route("GET", "/ws", websocket_handler)
app.on_shutdown.append(on_shutdown)
app.cleanup_ctx.append(run_detection_loop)
web.run_app(app, host="0.0.0.0", port=7860)