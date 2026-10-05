"""Low-voltage GPIO through the public Run-listener contract only."""

import time
from bloxsmith_app.block_api import APPLICATION_JSON,BlockDefinition,BlockRuntimeOutput,BlockRuntimePreparation,BlockRuntimeResult
from . import engine,logic,ui


# FB1 - Validate explicit chip offsets, low-voltage activation and bounded LED commands.
# FB2 - Listen from Active preparation, keep One Shot passive and own native process lifecycle.
# FB3 - Debounce sampled button changes and supervise finite LED patterns without stale bursts.
# FB4 - Restore configured rest before release on pause, errors, Stop and managed-host death.
# FB5 - Preserve public runtime contracts and responsive translated ES module surfaces.
class GpioBlock(BlockDefinition):
    kind = "gpio"

    def ui_assets(self,surface="modal"):
        return list(self.model.get("ui_assets",{}).get(surface,[]))

    def prepare_runtime(self,context):
        logic.configuration(context.config)
        return BlockRuntimePreparation(listen_on_run=context.runtime_mode=="zeromq_active")

    def result(self,context,values):
        return BlockRuntimeResult(status="success",outputs=[BlockRuntimeOutput(port_id=int(port.id),port_name=port.name,
            value=logic.encoded(values[port.name]).decode(),content_type=APPLICATION_JSON)
            for port in context.output_ports if port.name in values])

    def execute_runtime(self,context):
        if context.runtime_mode!="zeromq_active":
            return self.result(context,{"status":{"code":"preview","live":False,
                "detail":"Use Active Runtime. One Shot never opens a GPIO device."}})
        try:
            names = {int(port.id):port.name for port in context.input_ports}
            items = [(names.get(event.input_port_id),event.value) for event in context.input_events] if context.input_events else [
                (name,context.input_value(name,str(ident))) for ident,name in names.items() if context.has_input_value(name,str(ident))]
            if not items:
                return BlockRuntimeResult(status="skipped",outputs=[])
            if len(items)!=1 or items[0][0]!="command":
                logic.fail("Send one command per activation.")
            item = logic.command(items[0][1], logic.configuration(context.config))
            client = context.services.get("runtime_listener")
            if client is None:
                logic.fail("The listener command bridge is unavailable.")
            client.send(item)
            return BlockRuntimeResult(status="success",outputs=[])
        except (ValueError,TypeError,RuntimeError,OSError) as exc:
            return self.result(context,{"status":{"code":"command_rejected","detail":str(exc) if isinstance(exc,logic.GpioError) else "Command unavailable."}})

    def listen_runtime(self,context):
        config = logic.configuration(context.config)
        def emit(values):
            if not context.stop_requested():
                context.emit_result(self.result(context,values))
        listener = None
        try:
            listener = engine.Listener(config,emit)
            while not context.stop_requested():
                received = context.receive_command(timeout_sec=.02)
                if received is not None:
                    try:
                        listener.command(logic.command(dict(received.payload),config))
                    except (ValueError,TypeError,OSError):
                        emit({"status":{"code":"command_rejected"}})
                listener.tick(time.monotonic())
        except logic.GpioError as exc:
            emit({"status":{"code":"device_error","detail":str(exc)}})
            while not context.stop_requested():
                context.receive_command(timeout_sec=.1)
        finally:
            if listener is not None:
                listener.close()

    def handle_ui_action(self,*,node,action,values,payload=None):
        if action in {"save_settings","modal_update_fields","inspector_update_fields"}:
            patch = (values or {}).get("node_patch") or {}
            if "config" in patch:
                try:
                    logic.configuration({**self.default_config(),**(node.get("config") or {}),**patch["config"]})
                except (ValueError,TypeError) as exc:
                    return {"error":self.translate("block.gpio.error",{"detail":str(exc)},fallback="GPIO configuration: {detail}")}
        return ui.replace_config(super().handle_ui_action(node=node,
            action="modal_update_fields" if action=="save_settings" else action,values=values,payload=payload),node)

    def render_modal(self,*,node,payload=None):
        return ui.modal(self,node,payload)

    def render_inspector_panel(self,*,node,payload=None):
        return ui.inspector(self,node,payload)

    def render_node_card(self,*,node,payload=None):
        config = {**self.default_config(),**(node.get("config") or {})}
        return ui.card(self,node,config["chip"] + " · " + (self.translate("block.gpio.enabled",fallback="Enabled") if config["enabled"] else self.translate("block.gpio.disabled",fallback="Disabled")))
