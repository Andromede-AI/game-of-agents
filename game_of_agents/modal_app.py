from __future__ import annotations

import modal

from game_of_agents.modal_runtime import app, app_secret, data_volume, image


@app.function(
    image=image,
    volumes={"/goa_data": data_volume},
    secrets=[app_secret],
    env={"DATA_DIR": "/goa_data"},
)
@modal.asgi_app()
def fastapi_app():
    from game_of_agents.api import create_app

    return create_app(read_hook=data_volume.reload, write_hook=data_volume.commit)
