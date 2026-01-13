
import asyncio
import uuid
from collections import defaultdict
from typing import cast

from appdaemon import ADAPI
from appdaemon.entity import Entity
from appdaemon.exceptions import TimeOutException
from appdaemon.state import AsyncStateCallback


# noinspection PyInvalidCast
class AdWait:
    """Tool allowing an entity state update wait with timeout."""

    _asyncEvents: dict[str, asyncio.Event] = defaultdict(asyncio.Event)
    adapi: ADAPI

    def __init__(self, adapi: ADAPI):
        self.adapi = adapi
    # end __init__(ADAPI)

    async def waitUpdate(self, entity: Entity, timeout: int | float | None = None) -> None:
        """Wait for the update of an entity.

        :param entity: Entity to wait for update
        :param timeout: How long to wait for the update to occur before timing out
                When it times out, an appdaemon.exceptions.TimeOutException is raised
        """
        waitId = uuid.uuid4().hex
        asyncEvent = self._asyncEvents[waitId]

        try:
            handle = await entity.listen_state(
                cast(AsyncStateCallback, self._entityStateUpdated),
                attribute="all", oneshot=True, waitId=waitId)
            await asyncio.wait_for(asyncEvent.wait(), timeout=timeout)
        except asyncio.TimeoutError as e:
            # noinspection PyUnboundLocalVariable
            await self.adapi.cancel_listen_state(handle, entity.name)
            raise TimeOutException("The entity update timed out") from e
        finally:
            self._asyncEvents.pop(waitId, None)  # Ignore if already removed
    # end waitUpdate(Entity, int | float | None)

    async def _entityStateUpdated(self, entityId: str, _attribute: str,
                                  old: dict, new: dict, waitId: str, **_kwargs) -> None:
        """The entity state updated."""
        del old["entity_id"]
        del new["entity_id"]
        self.adapi.log("Updated: %s\nold=%s\nnew=%s", entityId, old, new)
        asyncEvent = self._asyncEvents.pop(waitId)
        # now release the wait
        asyncEvent.set()
    # end _entityStateUpdated(*Any, str, **Any)

# end class AdWait
