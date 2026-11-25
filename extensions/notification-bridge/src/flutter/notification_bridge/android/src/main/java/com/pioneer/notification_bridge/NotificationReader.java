package com.pioneer.notification_bridge;

import android.service.notification.NotificationListenerService;

public final class NotificationReader extends NotificationListenerService {
    private static volatile NotificationReader connected;

    static NotificationReader connected() {
        return connected;
    }

    @Override
    public void onListenerConnected() {
        connected = this;
    }

    @Override
    public void onListenerDisconnected() {
        if (connected == this) connected = null;
    }

    @Override
    public void onDestroy() {
        if (connected == this) connected = null;
        super.onDestroy();
    }
}
