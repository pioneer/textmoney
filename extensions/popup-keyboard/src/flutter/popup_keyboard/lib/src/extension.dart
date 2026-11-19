import 'package:flet/flet.dart';
import 'package:flet/src/controls/keyboard_listener.dart';
import 'package:flet/src/controls/container.dart';
import 'package:flutter/widgets.dart';

import 'category_menu_bounds.dart';
import 'popup_keyboard_scope.dart';
import 'popup_menu_back_scope.dart';

class Extension extends FletExtension {
  @override
  Widget? createWidget(Key? key, Control control) {
    if (control.type == 'PopupMenuBackScope') {
      return PopupMenuBackScope(
        key: key,
        child: ContainerControl(control: control),
      );
    }
    if (control.type == 'MobileCategoryMenu') {
      return CategoryMenuBounds(
        key: key,
        heightLimit: control.getDouble('menu_height_limit', 672)!,
        builder: (context, menuHeight, menuWidth, alignment) {
          final dropdown = control.child('content');
          if (dropdown == null || dropdown.type != 'Dropdown') {
            throw StateError('MobileCategoryMenu requires a Dropdown content.');
          }
          return ListenableBuilder(
            listenable: dropdown,
            builder: (context, child) {
              // Reapply after option or theme patches, without echoing client
              // geometry to Python or replacing the native dropdown state.
              final style = Map<String, dynamic>.from(
                dropdown.get('menu_style') as Map? ?? const {},
              );
              style['alignment'] = {'x': alignment.x, 'y': alignment.y};
              dropdown.updateProperties({
                'menu_height': menuHeight,
                'menu_width': menuWidth,
                'menu_style': style,
              }, python: false);
              return ContainerControl(control: control);
            },
          );
        },
      );
    }
    if (control.type != 'PopupKeyboardListener') return null;
    return PopupKeyboardScope(
      key: key,
      onEscape: () => control.triggerEvent('key_down', {'key': 'Escape'}),
      restoreFocus: control.getBool('restore_focus', false)!,
      child: KeyboardListenerControl(control: control),
    );
  }
}
