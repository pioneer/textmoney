import 'package:flet/flet.dart';
import 'package:flet/src/controls/keyboard_listener.dart';
import 'package:flutter/widgets.dart';

import 'popup_keyboard_scope.dart';

class Extension extends FletExtension {
  @override
  Widget? createWidget(Key? key, Control control) {
    if (control.type != 'PopupKeyboardListener') return null;
    return PopupKeyboardScope(
      key: key,
      onEscape: () => control.triggerEvent('key_down', {'key': 'Escape'}),
      restoreFocus: control.getBool('restore_focus', false)!,
      child: KeyboardListenerControl(control: control),
    );
  }
}
